"""Leakage-safe orchestration for the lean four-condition RQ3 experiment."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import math
import pathlib
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from .feedback import (
    BYTE_BUDGET_ID,
    DEFAULT_LIMITS,
    build_feedback_envelope,
    validate_feedback_envelope,
)
from .io_utils import atomic_write_json, canonical_json_hash, file_hash
from .rq3_base import base_generation_config
from .revise_skill import (
    RevisionError,
    package_hash,
    revision_config,
    revision_request,
    revise_skill_package,
    validate_revision_artifact,
    validate_skill_package,
)
from .model_policy import (
    DEFAULT_DEVELOPMENT_MODEL,
    require_experiment_model,
    skillgen_track_image,
)
from .scenario_contract import load_scenario, load_test
from .skill_eval import (
    HiddenExecutionRequest,
    execute_hidden_request,
    grade_run,
    raw_execution_artifacts,
    summarize_runs,
)


PUBLIC_ENTRIES = ("scenario.md", "base_skill", "campaign")
PRODUCERS = ("random", "greybox", "skillrace")
EVALUATION_CONDITIONS = (
    "zero-shot",
    "random-feedback",
    "greybox-feedback",
    "skillrace-feedback",
)
CONDITION_PRODUCER = {
    "random-feedback": "random",
    "greybox-feedback": "greybox",
    "skillrace-feedback": "skillrace",
}
RQ3_SCHEMA = "skillrace-rq3-manifest/1"
FROZEN_FEEDBACK_MAX_BYTES = 3600
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "client_secret",
    "access_token",
    "refresh_token",
    "credential",
    "credentials",
    "close_api_key",
}


class LeakageError(ValueError):
    """Raised when public staging or a post-stage audit crosses the hidden boundary."""


class ManifestMismatchError(ValueError):
    """Raised instead of resuming when any frozen RQ3 identity differs."""


class UncertainExternalOutcomeError(RuntimeError):
    """A durable start exists but no terminal artifact proves the external outcome."""


@dataclasses.dataclass(frozen=True)
class HiddenCaseIdentity:
    test_id: str
    root: pathlib.Path
    contract_identity: str
    candidate_hash: str
    dockerfile_hash: str
    checks_hash: str
    criterion_ids: tuple[str, ...]
    validation_evidence_hash: str
    validation_image_digest: str


@dataclasses.dataclass(frozen=True)
class HiddenScenarioIdentity:
    scenario_id: str
    contract_identity: str
    base_skill_hash: str
    tests: tuple[HiddenCaseIdentity, ...]


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ManifestMismatchError(f"{label} must be a lowercase SHA-256 hash")
    return value


def _require_image_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ManifestMismatchError(f"{label} must be a sha256:<lowercase-hex> image ID")
    return value


def _regular_tree(root: pathlib.Path, label: str) -> list[pathlib.Path]:
    if root.is_symlink():
        raise LeakageError(f"{label} symlink is forbidden: {root}")
    paths = [root, *sorted(root.rglob("*"))] if root.is_dir() else [root]
    files: list[pathlib.Path] = []
    for path in paths:
        if path.is_symlink():
            raise LeakageError(f"{label} symlink is forbidden: {path}")
        if path.is_file():
            files.append(path)
        elif path != root and not path.is_dir():
            raise LeakageError(f"{label} contains a non-regular entry: {path}")
    return files


def stage_public_scenario(
    source: str | pathlib.Path, destination: str | pathlib.Path
) -> pathlib.Path:
    """Physically copy only the three campaign-visible scenario artifact groups."""

    raw_source = pathlib.Path(source)
    if raw_source.is_symlink():
        raise LeakageError(f"scenario source symlink is forbidden: {raw_source}")
    source_root = raw_source.resolve()
    destination = pathlib.Path(destination)
    destination_parent = destination.parent.resolve()
    destination_resolved = (destination_parent / destination.name).resolve()
    if not source_root.is_dir():
        raise LeakageError(f"scenario source is not a directory: {source_root}")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    if destination_resolved == source_root or source_root in destination_resolved.parents:
        raise LeakageError("public stage must not be created inside the source scenario")
    entries: list[tuple[str, pathlib.Path]] = []
    for name in PUBLIC_ENTRIES:
        item = source_root / name
        if not item.exists():
            raise LeakageError(f"public scenario is missing required {name}")
        _regular_tree(item, f"public entry {name}")
        entries.append((name, item))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        stage = temporary / "stage"
        stage.mkdir()
        for name, item in entries:
            target = stage / name
            if item.is_dir():
                shutil.copytree(item, target)
            elif item.is_file():
                shutil.copy2(item, target)
            else:
                raise LeakageError(f"public entry must be a file or directory: {item}")
        files = {
            path.relative_to(stage).as_posix(): file_hash(path)
            for path in sorted(stage.rglob("*"))
            if path.is_file()
        }
        atomic_write_json(
            stage / "public-stage.json",
            {
                "schema": "skillrace-rq3-public-stage/1",
                "scenario_id": source_root.name,
                "files": files,
                "stage_hash": canonical_json_hash(files),
            },
        )
        # This scan uses public bytes only.  It never opens source/tests.
        forbidden = (b"tests/t", b"/tests/", b"../tests", str(source_root).encode())
        for path in _regular_tree(stage, "public stage"):
            data = path.read_bytes()
            if any(needle in data for needle in forbidden):
                raise LeakageError(f"public stage contains a forbidden hidden path: {path}")
        stage.rename(destination)
        temporary.rmdir()
        return destination
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _hidden_needles(hidden_root: pathlib.Path) -> tuple[tuple[str, bytes], ...]:
    hidden_root = hidden_root.resolve()
    if not hidden_root.is_dir():
        raise LeakageError(f"hidden root is not a directory: {hidden_root}")
    needles: list[tuple[str, bytes]] = [
        ("hidden root path", str(hidden_root).encode("utf-8")),
        ("hidden relative path", b"tests/t"),
    ]
    for path in _regular_tree(hidden_root, "hidden benchmark"):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest().encode("ascii")
        relative = pathlib.PurePosixPath("tests") / path.relative_to(hidden_root)
        needles.append((f"hidden hash {path.name}", digest))
        needles.append((f"hidden path {path.name}", str(path.resolve()).encode("utf-8")))
        needles.append((f"hidden relative path {path.name}", relative.as_posix().encode("utf-8")))
        if len(data) >= 12:
            needles.append((f"hidden bytes {path.name}", data))
        # Exact JSON string values catch a copied hidden prompt even when a public
        # artifact stores the prompt without the surrounding candidate document.
        if path.suffix == ".json":
            try:
                document = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                document = None

            def add_strings(value: Any) -> None:
                if isinstance(value, str):
                    encoded = value.encode("utf-8")
                    # Short identifiers (scenario names, schemas, status labels,
                    # shared image tags) legitimately occur on both sides of the
                    # boundary.  Long natural-language values and explicit sentinel
                    # markers are the hidden material worth detecting.
                    if (
                        len(encoded) >= 24
                        and (any(char.isspace() for char in value) or "sentinel" in value.lower())
                    ):
                        needles.append((f"hidden JSON value {path.name}", encoded))
                elif isinstance(value, Mapping):
                    for item in value.values():
                        add_strings(item)
                elif isinstance(value, list):
                    for item in value:
                        add_strings(item)

            add_strings(document)
        # Sentinel markers deliberately embedded in shell checks may be copied as
        # a log excerpt rather than as the complete check file.
        for marker in re.findall(rb"[A-Za-z0-9_-]*[Ss][Ee][Nn][Tt][Ii][Nn][Ee][Ll][A-Za-z0-9_-]*", data):
            if len(marker) >= 12:
                needles.append((f"hidden sentinel {path.name}", marker))
    return tuple(needles)


def assert_no_hidden_material(
    hidden_root: str | pathlib.Path,
    artifact_roots: Iterable[str | pathlib.Path],
) -> None:
    """Post-stage sentinel audit for public campaigns, feedback, revisions, and logs."""

    hidden = pathlib.Path(hidden_root).resolve()
    needles = _hidden_needles(hidden)
    for raw_root in artifact_roots:
        root = pathlib.Path(raw_root)
        if root.is_symlink():
            raise LeakageError(f"public artifact symlink is forbidden: {root}")
        resolved = root.resolve()
        if resolved == hidden or hidden in resolved.parents or resolved in hidden.parents:
            raise LeakageError(f"public artifact overlaps hidden benchmark: {root}")
        if not root.exists():
            raise LeakageError(f"public artifact is missing: {root}")
        for path in _regular_tree(root, "public artifact"):
            data = path.read_bytes()
            for label, needle in needles:
                if needle and needle in data:
                    raise LeakageError(f"public artifact contains hidden material ({label}): {path}")


def _read_artifact_object(path: pathlib.Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise ManifestMismatchError(f"{label} symlink is forbidden: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestMismatchError(f"cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ManifestMismatchError(f"{label} must contain a JSON object: {path}")
    return value


def _nonnegative_cost(value: Any, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ManifestMismatchError(f"{label} must be a finite non-negative cost")
    return float(value)


def campaign_record_from_file(
    path: str | pathlib.Path,
    *,
    expected_protocol_hash: str,
    expected_base_skill_hash: str,
    expected_model: str = DEFAULT_DEVELOPMENT_MODEL,
) -> dict[str, Any]:
    """Verify one frozen 30-run campaign and return a path-free manifest link."""

    path = pathlib.Path(path)
    campaign = _read_artifact_object(path, "campaign artifact")
    method = campaign.get("method")
    track_model = require_experiment_model(expected_model)
    if method not in PRODUCERS:
        raise ManifestMismatchError("campaign artifact has an invalid producer")
    if campaign.get("schema") == "campaign/2":
        from .rq3_campaign import CampaignArtifactError, validate_campaign_artifact

        try:
            return validate_campaign_artifact(
                path,
                expected_method=method,
                expected_protocol_hash=expected_protocol_hash,
                expected_base_skill_hash=expected_base_skill_hash,
                expected_model=track_model,
            )
        except CampaignArtifactError as error:
            raise ManifestMismatchError(f"invalid campaign/2 artifact: {error}") from error
    _require_digest(expected_protocol_hash, "expected protocol hash")
    _require_digest(expected_base_skill_hash, "expected base skill hash")
    if campaign.get("protocol_hash") != expected_protocol_hash:
        raise ManifestMismatchError(f"{method} campaign protocol hash mismatch")
    if campaign.get("base_skill_hash") != expected_base_skill_hash:
        raise ManifestMismatchError(f"{method} campaign base skill hash mismatch")
    attempts = campaign.get("attempts")
    if not isinstance(attempts, list):
        raise ManifestMismatchError(f"{method} campaign attempts are missing")
    counted = sum(
        row.get("consume_budget") is True for row in attempts if isinstance(row, Mapping)
    )
    totals = campaign.get("totals")
    totals = totals if isinstance(totals, Mapping) else {}
    expected_allocation = {
        "budget": 30,
        "bootstrap": 0 if method == "random" else 10,
        "exploration": 30 if method == "random" else 20,
    }
    if (
        campaign.get("budget") != 30
        or counted != 30
        or totals.get("runs") != 30
        or campaign.get("complete") is not True
    ):
        raise ManifestMismatchError(
            f"{method} campaign must be complete with exactly 30 counted executions"
        )
    if campaign.get("allocation") != expected_allocation:
        raise ManifestMismatchError(f"{method} campaign allocation mismatch")
    if (
        campaign.get("model") != track_model
        or campaign.get("agent_model") != track_model
    ):
        raise ManifestMismatchError(f"{method} campaign model mismatch")
    costs = campaign.get("costs")
    if not isinstance(costs, Mapping) or "total_provider_credits" not in costs:
        raise ManifestMismatchError(f"{method} campaign lacks complete cost accounting")
    cost = _nonnegative_cost(costs["total_provider_credits"], f"{method} campaign total_provider_credits")
    return {
        "artifact_hash": canonical_json_hash(campaign),
        "file_hash": file_hash(path),
        "protocol_hash": expected_protocol_hash,
        "base_skill_hash": expected_base_skill_hash,
        "budget": 30,
        "counted_executions": 30,
        "complete": True,
        "model": track_model,
        "agent_model": track_model,
        "allocation": expected_allocation,
        "cost_provider_credits": round(cost, 6),
    }


def feedback_record_from_file(
    path: str | pathlib.Path,
    *,
    expected_campaign_hash: str | None = None,
    expected_confirmation_hash: str | None = None,
) -> dict[str, Any]:
    """Verify an envelope and return the hashes used by revisions/manifests."""

    path = pathlib.Path(path)
    envelope = _read_artifact_object(path, "feedback envelope")
    try:
        validate_feedback_envelope(envelope)
    except ValueError as error:
        raise ManifestMismatchError(f"invalid feedback envelope: {error}") from error
    source_hash = envelope["accounting"]["source_campaign_hash"]
    if expected_campaign_hash is not None and source_hash != expected_campaign_hash:
        raise ManifestMismatchError("feedback source campaign hash mismatch")
    confirmation_hash = envelope["accounting"].get("source_confirmation_hash")
    if expected_confirmation_hash is not None and confirmation_hash != expected_confirmation_hash:
        raise ManifestMismatchError("feedback source confirmation hash mismatch")
    return {
        "artifact_hash": canonical_json_hash(envelope),
        "file_hash": file_hash(path),
        "source_campaign_hash": source_hash,
        "source_confirmation_hash": confirmation_hash,
        "schema": envelope["schema"],
        "budget_unit": envelope["accounting"]["budget_unit"],
        "max_bytes": envelope["accounting"]["max_bytes"],
        "used_bytes": envelope["accounting"]["used_bytes"],
        "limits": copy.deepcopy(dict(envelope["accounting"]["limits"])),
        "confirmation_executions": envelope["costs"]["confirmation_executions"],
        "confirmation_cost_provider_credits": envelope["costs"]["confirmation_cost_provider_credits"],
        "cost_provider_credits": 0.0,
    }


def revision_record_from_artifact(
    artifact_dir: str | pathlib.Path,
    *,
    expected_base_skill_hash: str,
    expected_envelope_hash: str,
    expected_model: str = DEFAULT_DEVELOPMENT_MODEL,
) -> tuple[dict[str, Any], pathlib.Path]:
    """Verify a revision artifact and return its manifest link and mount-only path."""

    artifact = pathlib.Path(artifact_dir)
    if artifact.is_symlink() or not artifact.is_dir():
        raise ManifestMismatchError(f"revision artifact is not a regular directory: {artifact}")
    record_path = artifact / "provenance" / "revision.json"
    try:
        record = validate_revision_artifact(
            artifact,
            expected_base_skill_hash=expected_base_skill_hash,
            expected_envelope_hash=expected_envelope_hash,
            expected_model=expected_model,
        )
    except RevisionError as error:
        raise ManifestMismatchError(f"invalid revision artifact: {error}") from error
    skill = validate_skill_package(artifact / "skill")
    cost = _nonnegative_cost(record.get("cost_provider_credits"), "revision cost_provider_credits")
    return (
        {
            "artifact_hash": canonical_json_hash(record),
            "file_hash": file_hash(record_path),
            "schema": record["schema"],
            "base_skill_hash": expected_base_skill_hash,
            "envelope_hash": expected_envelope_hash,
            "revised_skill_hash": record["revised_skill_hash"],
            "revised_package_hash": record["revised_package_hash"],
            "raw_response_hash": record["raw_response_hash"],
            "model_config": copy.deepcopy(dict(record["model_config"])),
            "operation_start_identity": copy.deepcopy(
                dict(record["operation_start_identity"])
            ),
            "operation_id": record["operation_id"],
            "request_hash": record["request_hash"],
            "provider_model": record["provider_model"],
            "provider_response_id_sha256": record["provider_response_id_sha256"],
            "provider_request_id_sha256": record["provider_request_id_sha256"],
            "billing_status": record["billing_status"],
            "journal_tag": record["journal_tag"],
            "journal_skill": record["journal_skill"],
            "journal_terminal_event_id": record["journal_terminal_event_id"],
            "journal_terminal_receipt": record["journal_terminal_receipt"],
            "journal_terminal_receipt_hash": record[
                "journal_terminal_receipt_hash"
            ],
            "journal_call_terminal_event_id": record[
                "journal_call_terminal_event_id"
            ],
            "journal_call_terminal_receipt": record[
                "journal_call_terminal_receipt"
            ],
            "journal_call_terminal_receipt_hash": record[
                "journal_call_terminal_receipt_hash"
            ],
            "input_tokens": int(record.get("input_tokens", 0) or 0),
            "output_tokens": int(record.get("output_tokens", 0) or 0),
            "cost_provider_credits": round(cost, 6),
        },
        skill,
    )


def project_feedback_set(
    *,
    campaign_paths: Mapping[str, str | pathlib.Path],
    confirmation_paths: Mapping[str, str | pathlib.Path] | None = None,
    out_dir: str | pathlib.Path,
    expected_protocol_hash: str,
    expected_base_skill_hash: str,
    expected_model: str = DEFAULT_DEVELOPMENT_MODEL,
    max_bytes: int = FROZEN_FEEDBACK_MAX_BYTES,
) -> tuple[dict[str, pathlib.Path], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Project the same frozen campaign set into three resumable neutral envelopes."""

    _exact_mapping(campaign_paths, PRODUCERS, "campaign producers")
    if confirmation_paths is not None:
        _exact_mapping(confirmation_paths, PRODUCERS, "confirmation producers")
    output = pathlib.Path(out_dir)
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise ManifestMismatchError(f"feedback output is not a regular directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    feedback_paths: dict[str, pathlib.Path] = {}
    envelope_records: dict[str, dict[str, Any]] = {}
    campaign_records: dict[str, dict[str, Any]] = {}
    for producer in PRODUCERS:
        campaign_path = pathlib.Path(campaign_paths[producer])
        campaign_record = campaign_record_from_file(
            campaign_path,
            expected_protocol_hash=expected_protocol_hash,
            expected_base_skill_hash=expected_base_skill_hash,
            expected_model=expected_model,
        )
        campaign = _read_artifact_object(campaign_path, "campaign artifact")
        if campaign.get("method") != producer:
            raise ManifestMismatchError(
                f"campaign mapping labels {campaign.get('method')!r} as {producer!r}"
            )
        confirmation = None
        confirmation_hash = None
        if confirmation_paths is not None:
            from .rq3_confirmation import validate_confirmation_ledger

            confirmation = validate_confirmation_ledger(
                confirmation_paths[producer]
            )
            if confirmation.get("source_campaign_hash") != campaign_record["artifact_hash"]:
                raise ManifestMismatchError(
                    f"{producer} confirmation/campaign hash mismatch"
                )
            confirmation_hash = canonical_json_hash(confirmation)
        envelope = build_feedback_envelope(
            campaign, max_bytes=max_bytes, confirmations=confirmation
        )
        target = output / f"{producer}.json"
        expected_envelope_hash = canonical_json_hash(envelope)
        if target.exists():
            try:
                existing = feedback_record_from_file(
                    target,
                    expected_campaign_hash=campaign_record["artifact_hash"],
                    expected_confirmation_hash=confirmation_hash,
                )
            except ManifestMismatchError as error:
                raise ManifestMismatchError(
                    f"stale feedback envelope for {producer}: {error}"
                ) from error
            if existing["artifact_hash"] != expected_envelope_hash:
                raise ManifestMismatchError(f"stale feedback envelope for {producer}")
        else:
            atomic_write_json(target, envelope)
        feedback_paths[producer] = target
        envelope_records[producer] = feedback_record_from_file(
            target,
            expected_campaign_hash=campaign_record["artifact_hash"],
            expected_confirmation_hash=confirmation_hash,
        )
        campaign_records[producer] = campaign_record
    return feedback_paths, envelope_records, campaign_records


def revise_feedback_set(
    *,
    base_skill_dir: str | pathlib.Path,
    feedback_paths: Mapping[str, str | pathlib.Path],
    out_dir: str | pathlib.Path,
    chat_fn: Callable[..., Mapping[str, Any]],
    model: str = DEFAULT_DEVELOPMENT_MODEL,
) -> tuple[dict[str, dict[str, Any]], dict[str, pathlib.Path]]:
    """Create or verify the three blind revisions without repeating model calls."""

    _exact_mapping(feedback_paths, PRODUCERS, "feedback producers")
    base = validate_skill_package(base_skill_dir)
    base_hash = file_hash(base / "SKILL.md")
    track_model = require_experiment_model(model)
    output = pathlib.Path(out_dir)
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise ManifestMismatchError(f"revision output is not a regular directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    records: dict[str, dict[str, Any]] = {}
    skills: dict[str, pathlib.Path] = {"zero-shot": base}
    for producer in PRODUCERS:
        envelope_path = pathlib.Path(feedback_paths[producer])
        envelope_record = feedback_record_from_file(envelope_path)
        envelope = _read_artifact_object(envelope_path, "feedback envelope")
        target = output / producer
        start_path = output / f"{producer}.start.json"
        receipt_path = output / f"{producer}.receipt.json"
        request = revision_request(
            (base / "SKILL.md").read_text(encoding="utf-8"),
            envelope,
            model=track_model,
        )
        start_payload = {
            "schema": "skillrace-revision-start/1",
            "producer": producer,
            "base_skill_hash": base_hash,
            "base_package_hash": package_hash(base),
            "envelope_hash": envelope_record["artifact_hash"],
            "request_hash": canonical_json_hash(request),
            "model_config": revision_config(track_model),
        }
        if receipt_path.exists() and not target.exists():
            raise ManifestMismatchError(
                f"completed revision artifact is missing for {producer}"
            )
        if target.exists() and not start_path.exists():
            raise ManifestMismatchError(f"revision start record is missing for {producer}")
        if start_path.exists():
            existing_start = _read_artifact_object(start_path, "revision start")
            if existing_start != start_payload:
                raise ManifestMismatchError(f"revision start identity mismatch for {producer}")
            if not target.exists():
                raise UncertainExternalOutcomeError(
                    f"revision outcome is unknown for {producer}; durable start exists "
                    "without a terminal artifact"
                )
        else:
            atomic_write_json(start_path, start_payload)
        if not target.exists():
            revise_skill_package(
                base, envelope, target, model=track_model, chat_fn=chat_fn
            )
        record, skill = revision_record_from_artifact(
            target,
            expected_base_skill_hash=base_hash,
            expected_envelope_hash=envelope_record["artifact_hash"],
            expected_model=track_model,
        )
        start_hash = file_hash(start_path)
        receipt_payload = {
            "schema": "skillrace-revision-receipt/1",
            "producer": producer,
            "start_hash": start_hash,
            "revision_record_hash": record["artifact_hash"],
            "revision_record_file_hash": record["file_hash"],
            "revised_package_hash": record["revised_package_hash"],
            "raw_response_hash": record["raw_response_hash"],
        }
        if receipt_path.exists():
            existing_receipt = _read_artifact_object(
                receipt_path, "revision receipt"
            )
            if existing_receipt != receipt_payload:
                raise ManifestMismatchError(
                    f"revision receipt hash mismatch for {producer}"
                )
        else:
            atomic_write_json(receipt_path, receipt_payload)
        record["start_hash"] = start_hash
        record["receipt_hash"] = file_hash(receipt_path)
        records[producer] = record
        skills[f"{producer}-feedback"] = skill
    # The producer name `greybox` already maps to the frozen condition spelling.
    return records, {condition: skills[condition] for condition in EVALUATION_CONDITIONS}


def _load_hidden_scenario_identity(
    scenario_dir: str | pathlib.Path,
) -> HiddenScenarioIdentity:
    """Resolve hidden paths only at the start of the evaluation phase."""

    scenario = load_scenario(scenario_dir)
    rows: list[HiddenCaseIdentity] = []
    for test_id in scenario.expected_test_ids:
        name = test_id.rsplit("/", 1)[-1]
        hidden = load_test(scenario.hidden_tests_dir / name)
        checks_hash = canonical_json_hash(
            [
                {
                    "id": criterion.id,
                    "script_sha256": criterion.script_sha256,
                    "kind": criterion.kind,
                    "expected_status": criterion.expected_status,
                    "expected_output": criterion.expected_output,
                }
                for criterion in hidden.criteria
            ]
        )
        rows.append(
            HiddenCaseIdentity(
                test_id=hidden.test_id,
                root=hidden.root,
                contract_identity=hidden.contract_identity_sha256,
                candidate_hash=hidden.candidate_sha256,
                dockerfile_hash=hidden.dockerfile_sha256,
                checks_hash=checks_hash,
                criterion_ids=tuple(criterion.id for criterion in hidden.criteria),
                validation_evidence_hash=file_hash(hidden.evidence.path),
                validation_image_digest=_require_image_digest(
                    hidden.evidence.payload.get("image_digest"),
                    f"{hidden.test_id} validation image digest",
                ),
            )
        )
    return HiddenScenarioIdentity(
        scenario_id=scenario.scenario_id,
        contract_identity=file_hash(scenario.root / "scenario.json"),
        base_skill_hash=scenario.base_skill_sha256,
        tests=tuple(rows),
    )


def _reject_secret_fields(value: Any, path: str = "manifest inputs") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SECRET_KEYS or normalized.endswith("_api_key"):
                raise ManifestMismatchError(f"secret field is forbidden at {path}.{key}")
            _reject_secret_fields(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_secret_fields(item, f"{path}[{index}]")


def _exact_mapping(value: Mapping[str, Any], expected: Sequence[str], label: str) -> None:
    if len(value) != len(expected) or set(value) != set(expected):
        raise ManifestMismatchError(
            f"{label} must contain exactly {list(expected)}; got {list(value)}"
        )


def _validate_inputs(
    protocol_hash: str,
    base_skill: Mapping[str, Any],
    campaigns: Mapping[str, Mapping[str, Any]],
    envelopes: Mapping[str, Mapping[str, Any]],
    revisions: Mapping[str, Mapping[str, Any]],
    skills_by_condition: Mapping[str, pathlib.Path],
    model_config: Mapping[str, Any],
) -> None:
    _require_digest(protocol_hash, "protocol_hash")
    _exact_mapping(campaigns, PRODUCERS, "campaign producers")
    _exact_mapping(envelopes, PRODUCERS, "feedback producers")
    _exact_mapping(revisions, PRODUCERS, "revision producers")
    _exact_mapping(skills_by_condition, EVALUATION_CONDITIONS, "evaluation conditions")
    _reject_secret_fields(
        {
            "base_skill": base_skill,
            "campaigns": campaigns,
            "envelopes": envelopes,
            "revisions": revisions,
            "model_config": model_config,
        }
    )
    try:
        track_model = require_experiment_model(model_config.get("model"))
    except ValueError as error:
        raise ManifestMismatchError("hidden evaluator model is not a selected track") from error
    base_hash = _require_digest(base_skill.get("skill_hash"), "base skill_hash")
    _require_digest(base_skill.get("artifact_hash"), "base artifact_hash")
    if base_skill.get("schema") != "skillrace-base-generation/2":
        raise ManifestMismatchError(
            "base generation schema must be skillrace-base-generation/2"
        )
    _require_digest(base_skill.get("package_hash"), "base package_hash")
    generation_id = base_skill.get("generation_id")
    if (
        not isinstance(generation_id, str)
        or not re.fullmatch(r"[0-9a-f]{24}", generation_id)
    ):
        raise ManifestMismatchError("base generation_id must be 24 lowercase hex characters")
    if base_skill.get("model_config") != base_generation_config(track_model):
        raise ManifestMismatchError("base generation model configuration mismatch")
    operation_id = base_skill.get("operation_id")
    if (
        not isinstance(operation_id, str)
        or not operation_id.startswith("rq3.base.")
        or not SHA256_RE.fullmatch(operation_id.removeprefix("rq3.base."))
    ):
        raise ManifestMismatchError("base generation operation identity mismatch")
    if (
        base_skill.get("provider_model") != track_model
        or base_skill.get("billing_status") != "known"
    ):
        raise ManifestMismatchError("base generation provider provenance mismatch")
    _require_digest(
        base_skill.get("provider_response_id_sha256"),
        "base provider response identity",
    )
    base_provider_request = base_skill.get("provider_request_id_sha256")
    if base_provider_request is not None:
        _require_digest(base_provider_request, "base provider request identity")
    for field in (
        "journal_terminal_event_id",
        "journal_terminal_receipt_hash",
        "journal_call_terminal_event_id",
        "journal_call_terminal_receipt_hash",
    ):
        _require_digest(base_skill.get(field), f"base generation {field}")
    if (
        base_skill.get("journal_terminal_receipt")
        != ".skillrace/model-call-terminal.json"
        or base_skill.get("journal_call_terminal_receipt")
        != ".skillrace/model-call-operation-terminal.json"
    ):
        raise ManifestMismatchError("base generation journal receipt path mismatch")
    if not isinstance(model_config.get("wall_clock"), int) or model_config["wall_clock"] <= 0:
        raise ManifestMismatchError("model_config.wall_clock must be a positive integer")
    for producer in PRODUCERS:
        campaign = campaigns[producer]
        campaign_hash = _require_digest(
            campaign.get("artifact_hash"), f"{producer} campaign artifact_hash"
        )
        if campaign.get("protocol_hash") != protocol_hash:
            raise ManifestMismatchError(f"{producer} campaign protocol hash mismatch")
        if campaign.get("base_skill_hash") != base_hash:
            raise ManifestMismatchError(f"{producer} campaign base skill hash mismatch")
        if campaign.get("budget") != 30:
            raise ManifestMismatchError(f"{producer} campaign budget must be exactly 30")
        if campaign.get("counted_executions") != 30 or campaign.get("complete") is not True:
            raise ManifestMismatchError(
                f"{producer} campaign must be complete with exactly 30 counted executions"
            )
        if (
            campaign.get("model") != track_model
            or campaign.get("agent_model") != track_model
        ):
            raise ManifestMismatchError(
                f"{producer} campaign must use the same frozen track model"
            )
        expected_allocation = {
            "budget": 30,
            "bootstrap": 0 if producer == "random" else 10,
            "exploration": 30 if producer == "random" else 20,
        }
        if campaign.get("allocation") != expected_allocation:
            raise ManifestMismatchError(
                f"{producer} campaign allocation differs from the frozen 30-run protocol"
            )
        envelope = envelopes[producer]
        envelope_hash = _require_digest(
            envelope.get("artifact_hash"), f"{producer} envelope artifact_hash"
        )
        if envelope.get("source_campaign_hash") != campaign_hash:
            raise ManifestMismatchError(f"{producer} envelope/campaign hash mismatch")
        if envelope.get("budget_unit") != BYTE_BUDGET_ID:
            raise ManifestMismatchError(
                f"{producer} feedback byte-budget unit differs from the frozen protocol"
            )
        if envelope.get("max_bytes") != FROZEN_FEEDBACK_MAX_BYTES:
            raise ManifestMismatchError(
                f"{producer} feedback max_bytes must be exactly "
                f"{FROZEN_FEEDBACK_MAX_BYTES}"
            )
        if envelope.get("limits") != DEFAULT_LIMITS:
            raise ManifestMismatchError(
                f"{producer} feedback limits differ from the frozen protocol"
            )
        used_bytes = envelope.get("used_bytes")
        if (
            isinstance(used_bytes, bool)
            or not isinstance(used_bytes, int)
            or used_bytes <= 0
            or used_bytes > FROZEN_FEEDBACK_MAX_BYTES
        ):
            raise ManifestMismatchError(f"{producer} feedback used_bytes is invalid")
        confirmation_executions = envelope.get("confirmation_executions")
        if (
            isinstance(confirmation_executions, bool)
            or not isinstance(confirmation_executions, int)
            or confirmation_executions < 0
        ):
            raise ManifestMismatchError(
                f"{producer} feedback confirmation execution count is invalid"
            )
        _nonnegative_cost(
            envelope.get("confirmation_cost_provider_credits"),
            f"{producer} feedback confirmation_cost_provider_credits",
        )
        revision = revisions[producer]
        if revision.get("schema") != "skillrace-revision/2":
            raise ManifestMismatchError(
                f"{producer} revision schema must be skillrace-revision/2"
            )
        _require_digest(revision.get("artifact_hash"), f"{producer} revision artifact_hash")
        if revision.get("base_skill_hash") != base_hash:
            raise ManifestMismatchError(f"{producer} revision base skill hash mismatch")
        if revision.get("envelope_hash") != envelope_hash:
            raise ManifestMismatchError(f"{producer} revision envelope hash mismatch")
        _require_digest(
            revision.get("revised_skill_hash"), f"{producer} revised skill hash"
        )
        if revision.get("model_config") != revision_config(track_model):
            raise ManifestMismatchError(
                f"{producer} revision model configuration differs from the frozen reviser"
            )
        start_identity = revision.get("operation_start_identity")
        if not isinstance(start_identity, Mapping):
            raise ManifestMismatchError(
                f"{producer} revision operation start identity is missing"
            )
        request_hash = _require_digest(
            revision.get("request_hash"), f"{producer} revision request_hash"
        )
        expected_start = {
            "schema": "skillrace-revision-start/1",
            "producer": producer,
            "base_skill_hash": base_hash,
            "base_package_hash": _require_digest(
                start_identity.get("base_package_hash"),
                f"{producer} revision base_package_hash",
            ),
            "envelope_hash": envelope_hash,
            "request_hash": request_hash,
            "model_config": revision_config(track_model),
        }
        if dict(start_identity) != expected_start:
            raise ManifestMismatchError(
                f"{producer} revision operation start identity mismatch"
            )
        if revision.get("operation_id") != (
            f"rq3.revision.{canonical_json_hash(expected_start)}"
        ):
            raise ManifestMismatchError(f"{producer} revision operation identity mismatch")
        if (
            revision.get("provider_model") != track_model
            or revision.get("billing_status") != "known"
            or revision.get("journal_tag") != "rq3.revise"
            or not isinstance(revision.get("journal_skill"), str)
            or not revision.get("journal_skill")
        ):
            raise ManifestMismatchError(
                f"{producer} revision provider/journal provenance mismatch"
            )
        _require_digest(
            revision.get("provider_response_id_sha256"),
            f"{producer} revision provider response identity",
        )
        provider_request = revision.get("provider_request_id_sha256")
        if provider_request is not None:
            _require_digest(
                provider_request, f"{producer} revision provider request identity"
            )
        for field in (
            "journal_terminal_event_id",
            "journal_terminal_receipt_hash",
            "journal_call_terminal_event_id",
            "journal_call_terminal_receipt_hash",
        ):
            _require_digest(revision.get(field), f"{producer} revision {field}")
        if (
            revision.get("journal_terminal_receipt")
            != "provenance/model-call-terminal.json"
            or revision.get("journal_call_terminal_receipt")
            != "provenance/model-call-operation-terminal.json"
        ):
            raise ManifestMismatchError(
                f"{producer} revision journal receipt path mismatch"
            )


def _skill_hashes(
    base_skill: Mapping[str, Any],
    revisions: Mapping[str, Mapping[str, Any]],
    skills: Mapping[str, pathlib.Path],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for condition in EVALUATION_CONDITIONS:
        root = validate_skill_package(skills[condition])
        actual = file_hash(root / "SKILL.md")
        expected = (
            base_skill["skill_hash"]
            if condition == "zero-shot"
            else revisions[CONDITION_PRODUCER[condition]]["revised_skill_hash"]
        )
        if actual != expected:
            raise ManifestMismatchError(f"{condition} mounted skill hash mismatch")
        result[condition] = actual
    return result


def _normalized_repair_records(
    repairs: Mapping[str, Mapping[str, Any]] | None,
    campaigns: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if repairs is None:
        return {
            producer: {
                "schema": "skillrace-failure-repairs/1",
                "method": producer,
                "source_campaign_hash": campaigns[producer].get("artifact_hash"),
                "failed_public_executions": 0,
                "repair_executions": 0,
                "repair_executions_counted_in_search_budget": False,
                "repairs": [],
                "costs": {"patch_provider_credits": 0.0, "replay_provider_credits": 0.0, "total_provider_credits": 0.0},
            }
            for producer in PRODUCERS
        }
    _exact_mapping(repairs, PRODUCERS, "repair producers")
    normalized: dict[str, dict[str, Any]] = {}
    for producer in PRODUCERS:
        record = repairs[producer]
        if (
            not isinstance(record, Mapping)
            or record.get("schema") != "skillrace-failure-repairs/1"
            or record.get("method") != producer
            or record.get("repair_executions_counted_in_search_budget") is not False
            or not isinstance(record.get("repairs"), list)
            or record.get("repair_executions") != len(record["repairs"])
        ):
            raise ManifestMismatchError(f"{producer} repair record is malformed")
        costs = record.get("costs")
        if not isinstance(costs, Mapping):
            raise ManifestMismatchError(f"{producer} repair cost record is malformed")
        _nonnegative_cost(costs.get("total_provider_credits"), f"{producer} repair total cost")
        normalized[producer] = copy.deepcopy(dict(record))
    _reject_secret_fields(normalized, "repair records")
    return normalized


def _manifest_hash(value: Mapping[str, Any]) -> str:
    return canonical_json_hash({key: item for key, item in value.items() if key != "manifest_hash"})


def _write_manifest(path: pathlib.Path, manifest: dict[str, Any]) -> None:
    manifest.pop("manifest_hash", None)
    manifest["manifest_hash"] = _manifest_hash(manifest)
    atomic_write_json(path, manifest)


def load_rq3_manifest(
    path: str | pathlib.Path, *, expected_protocol_hash: str | None = None
) -> dict[str, Any]:
    """Load a manifest only when its self-hash and optional protocol identity match."""

    path = pathlib.Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ManifestMismatchError(f"cannot load RQ3 manifest: {path}") from error
    if not isinstance(value, dict) or value.get("schema") != RQ3_SCHEMA:
        raise ManifestMismatchError("unsupported RQ3 manifest schema")
    if value.get("manifest_hash") != _manifest_hash(value):
        raise ManifestMismatchError("RQ3 manifest hash mismatch")
    if expected_protocol_hash is not None and value.get("protocol_hash") != expected_protocol_hash:
        raise ManifestMismatchError("RQ3 protocol hash mismatch")
    _reject_secret_fields(value)
    return value


def _execution_identity(
    protocol_hash: str,
    scenario_id: str,
    replication: int,
    condition: str,
    test: HiddenCaseIdentity,
    skill_hash: str,
    model_config: Mapping[str, Any],
) -> tuple[str, str]:
    request = {
        "protocol_hash": protocol_hash,
        "scenario_id": scenario_id,
        "replication": replication,
        "test_id": test.test_id,
        "test_contract_identity": test.contract_identity,
        "candidate_hash": test.candidate_hash,
        "dockerfile_hash": test.dockerfile_hash,
        "checks_hash": test.checks_hash,
        "criterion_ids": list(test.criterion_ids),
        "validation_evidence_hash": test.validation_evidence_hash,
        "validation_image_digest": test.validation_image_digest,
        "skill_hash": skill_hash,
        "model_config": dict(model_config),
    }
    request_hash = canonical_json_hash(request)
    execution_id = canonical_json_hash(
        {"condition": condition, "request_hash": request_hash}
    )[:24]
    return execution_id, request_hash


def _new_manifest(
    hidden: HiddenScenarioIdentity,
    protocol_hash: str,
    replication: int,
    base_skill: Mapping[str, Any],
    campaigns: Mapping[str, Mapping[str, Any]],
    repairs: Mapping[str, Mapping[str, Any]],
    envelopes: Mapping[str, Mapping[str, Any]],
    revisions: Mapping[str, Mapping[str, Any]],
    skill_hashes: Mapping[str, str],
    model_config: Mapping[str, Any],
) -> dict[str, Any]:
    rq3_id = canonical_json_hash(
        {
            "protocol_hash": protocol_hash,
            "scenario_id": hidden.scenario_id,
            "replication": replication,
            "base_skill_hash": base_skill["skill_hash"],
        }
    )[:24]
    evaluations: dict[str, Any] = {}
    for condition in EVALUATION_CONDITIONS:
        test_rows: dict[str, Any] = {}
        for test in hidden.tests:
            execution_id, request_hash = _execution_identity(
                protocol_hash,
                hidden.scenario_id,
                replication,
                condition,
                test,
                skill_hashes[condition],
                model_config,
            )
            test_rows[test.test_id] = {
                "execution_id": execution_id,
                "request_hash": request_hash,
                "test_contract_identity": test.contract_identity,
                "candidate_hash": test.candidate_hash,
                "dockerfile_hash": test.dockerfile_hash,
                "checks_hash": test.checks_hash,
                "criterion_ids": list(test.criterion_ids),
                "validation_evidence_hash": test.validation_evidence_hash,
                "validation_image_digest": test.validation_image_digest,
                "execution_count": 0,
                "status": "pending",
                "start_hash": None,
                "result_hash": None,
                "receipt_hash": None,
                "raw_artifacts_hash": None,
                "agent_id": None,
                "grade": None,
            }
        evaluations[condition] = {
            "skill_hash": skill_hashes[condition],
            "tests": test_rows,
            "summary": None,
        }
    manifest = {
        "schema": RQ3_SCHEMA,
        "protocol_hash": protocol_hash,
        "rq3_id": rq3_id,
        "scenario_id": hidden.scenario_id,
        "replication": replication,
        "one_execution_per_hidden_test": True,
        "base_skill": copy.deepcopy(dict(base_skill)),
        "campaigns": {name: copy.deepcopy(dict(campaigns[name])) for name in PRODUCERS},
        "repairs": {name: copy.deepcopy(dict(repairs[name])) for name in PRODUCERS},
        "feedback_envelopes": {
            name: copy.deepcopy(dict(envelopes[name])) for name in PRODUCERS
        },
        "revisions": {name: copy.deepcopy(dict(revisions[name])) for name in PRODUCERS},
        "scenario_contract_identity": hidden.contract_identity,
        "scenario_base_skill_hash": hidden.base_skill_hash,
        "model_config": copy.deepcopy(dict(model_config)),
        "evaluations": evaluations,
        "costs": {
            "campaign_provider_credits": round(
                sum(float(campaigns[name].get("cost_provider_credits", 0.0) or 0.0) for name in PRODUCERS),
                6,
            ),
            "confirmation_provider_credits": round(
                sum(
                    float(envelopes[name].get("confirmation_cost_provider_credits", 0.0) or 0.0)
                    for name in PRODUCERS
                ),
                6,
            ),
            "repair_provider_credits": round(
                sum(
                    float(repairs[name].get("costs", {}).get("total_provider_credits", 0.0) or 0.0)
                    for name in PRODUCERS
                ),
                6,
            ),
            "revision_provider_credits": round(
                sum(float(revisions[name].get("cost_provider_credits", 0.0) or 0.0) for name in PRODUCERS),
                6,
            ),
            "evaluation_provider_credits": 0.0,
            "total_provider_credits": 0.0,
        },
    }
    manifest["costs"]["total_provider_credits"] = round(
        manifest["costs"]["campaign_provider_credits"]
        + manifest["costs"]["confirmation_provider_credits"]
        + manifest["costs"]["repair_provider_credits"]
        + manifest["costs"]["revision_provider_credits"],
        6,
    )
    manifest["manifest_hash"] = _manifest_hash(manifest)
    return manifest


def _assert_resume_identity(current: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    scalar_fields = (
        "schema",
        "protocol_hash",
        "rq3_id",
        "scenario_id",
        "replication",
        "one_execution_per_hidden_test",
        "base_skill",
        "campaigns",
        "repairs",
        "feedback_envelopes",
        "revisions",
        "scenario_contract_identity",
        "scenario_base_skill_hash",
        "model_config",
    )
    for field in scalar_fields:
        if current.get(field) != expected.get(field):
            raise ManifestMismatchError(f"RQ3 resume identity mismatch for {field}")
    if tuple(current.get("evaluations", {})) != EVALUATION_CONDITIONS:
        raise ManifestMismatchError("RQ3 resume conditions mismatch")
    for condition in EVALUATION_CONDITIONS:
        actual_condition = current["evaluations"][condition]
        expected_condition = expected["evaluations"][condition]
        if actual_condition.get("skill_hash") != expected_condition.get("skill_hash"):
            raise ManifestMismatchError(f"{condition} skill hash mismatch on resume")
        if tuple(actual_condition.get("tests", {})) != tuple(expected_condition["tests"]):
            raise ManifestMismatchError(f"{condition} hidden test set mismatch on resume")
        for test_id, expected_row in expected_condition["tests"].items():
            actual_row = actual_condition["tests"][test_id]
            for field in (
                "execution_id",
                "request_hash",
                "test_contract_identity",
                "candidate_hash",
                "dockerfile_hash",
                "checks_hash",
                "criterion_ids",
                "validation_evidence_hash",
                "validation_image_digest",
            ):
                if actual_row.get(field) != expected_row[field]:
                    raise ManifestMismatchError(
                        f"{condition}/{test_id} identity mismatch for {field}"
                    )


def _read_json_object(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ManifestMismatchError(f"cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ManifestMismatchError(f"{label} must contain a JSON object: {path}")
    return value


def _validate_result(
    result: Mapping[str, Any],
    execution_id: str,
    request_hash: str,
    *,
    test: HiddenCaseIdentity,
    run_root: pathlib.Path,
) -> dict[str, Any]:
    if result.get("schema") != "skillrace-hidden-result/1":
        raise ManifestMismatchError("hidden result schema mismatch")
    if result.get("execution_id") != execution_id or result.get("request_hash") != request_hash:
        raise ManifestMismatchError("hidden result execution/request identity mismatch")
    if result.get("status") not in {"completed", "timeout", "error", "inconclusive"}:
        raise ManifestMismatchError("hidden result status mismatch")
    execution_status = result.get("execution_status")
    if execution_status not in {"completed", "timeout", "error", "inconclusive"}:
        raise ManifestMismatchError("hidden result execution status mismatch")
    expected_identity = {
        "test_id": test.test_id,
        "test_contract_identity": test.contract_identity,
        "validation_evidence_hash": test.validation_evidence_hash,
        "validation_image_digest": test.validation_image_digest,
        "criterion_ids": list(test.criterion_ids),
    }
    for field, expected in expected_identity.items():
        if result.get(field) != expected:
            raise ManifestMismatchError(f"hidden result identity mismatch for {field}")
    try:
        current_raw = raw_execution_artifacts(
            run_root / "agent" / "execution",
            path_prefix="agent/execution",
        )
    except ValueError as error:
        raise ManifestMismatchError(f"invalid raw hidden artifact: {error}") from error
    if result.get("raw_artifacts") != current_raw:
        raise ManifestMismatchError("raw hidden artifact hash mismatch")
    raw_hash = canonical_json_hash(current_raw)
    if result.get("raw_artifacts_hash") != raw_hash:
        raise ManifestMismatchError("raw hidden artifact inventory hash mismatch")
    if result.get("launch_hash") != current_raw["launch"]["sha256"]:
        raise ManifestMismatchError("raw launch hash/result mismatch")
    if any(record["sha256"] is None for record in current_raw.values()):
        raise ManifestMismatchError("hidden result lacks required raw execution artifacts")

    verdict_path = run_root / "agent" / "execution" / "verdicts.json"
    if verdict_path.is_file():
        try:
            raw_verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ManifestMismatchError("raw hidden verdicts are malformed") from error
        if raw_verdicts != result.get("verdicts"):
            raise ManifestMismatchError("raw hidden verdicts/result mismatch")

    cost_path = run_root / "agent" / "execution" / "cost.json"
    if cost_path.is_file():
        raw_cost = _read_json_object(cost_path, "raw hidden cost")
        expected_cost = float(
            raw_cost.get(
                "cost_provider_credits",
                raw_cost.get("provider_credits", raw_cost.get("price_provider_credits", 0.0)),
            )
            or 0.0
        )
        if (
            result.get("input_tokens") != int(raw_cost.get("in", 0) or 0)
            or result.get("output_tokens") != int(raw_cost.get("out", 0) or 0)
            or result.get("cost_provider_credits") != expected_cost
        ):
            raise ManifestMismatchError("raw hidden cost/result mismatch")

    run_path = run_root / "agent" / "execution" / "run.json"
    if run_path.is_file():
        raw_run = _read_json_object(run_path, "raw hidden run")
        if result.get("run_id") != raw_run.get("run_id"):
            raise ManifestMismatchError("raw hidden run/result identity mismatch")
        start = _read_json_object(run_root / "start.json", "hidden execution start")
        model_config = start.get("model_config")
        model = model_config.get("model") if isinstance(model_config, Mapping) else None
        if (
            raw_run.get("base_image") != skillgen_track_image(str(model))
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(raw_run.get("base_image_id", ""))
            )
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(raw_run.get("env_image_id", ""))
            )
        ):
            raise ManifestMismatchError("raw hidden run model-runtime identity mismatch")
        termination = raw_run.get("termination")
        if isinstance(termination, Mapping):
            seconds = float(termination.get("seconds", 0.0) or 0.0)
            if result.get("wall_seconds") != seconds:
                raise ManifestMismatchError("raw hidden run/result wall time mismatch")

    verdicts = result.get("verdicts")
    if not isinstance(verdicts, list) or any(
        not isinstance(row, Mapping) for row in verdicts
    ):
        raise ManifestMismatchError("hidden result verdicts are malformed")
    recomputed = grade_run(
        verdicts,
        execution_status=str(execution_status),
        expected_criterion_ids=test.criterion_ids,
    )
    # Preserve the executor status separately: a completed execution can still
    # receive an inconclusive grade when one exact hidden criterion is unavailable.
    if result.get("grade") != recomputed:
        raise ManifestMismatchError("hidden result grade does not match recomputation")
    return recomputed


def _receipt_payload(
    execution_id: str,
    request_hash: str,
    start_hash: str,
    result_hash: str,
    validation_image_digest: str,
    criterion_ids: Sequence[str],
    raw_artifacts_hash: str,
    *,
    recovered: bool,
) -> dict[str, Any]:
    return {
        "schema": "skillrace-hidden-receipt/1",
        "execution_id": execution_id,
        "request_hash": request_hash,
        "start_hash": start_hash,
        "result_hash": result_hash,
        "validation_image_digest": validation_image_digest,
        "criterion_ids": list(criterion_ids),
        "raw_artifacts_hash": raw_artifacts_hash,
        "execution_count": 1,
        "recovered_from_committed_result": recovered,
    }


def _normalize_executor_result(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {
            "status": "error",
            "verdicts": [],
            "error_type": "MalformedExecutorResult",
            "error_message": "executor did not return an object",
        }
    status = raw.get("status")
    if status not in {"completed", "timeout", "error", "inconclusive"}:
        status = "error"
    verdicts = raw.get("verdicts")
    verdicts = [dict(row) for row in verdicts if isinstance(row, Mapping)] if isinstance(verdicts, list) else []
    return {
        "status": status,
        "verdicts": verdicts,
        "input_tokens": int(raw.get("input_tokens", 0) or 0),
        "output_tokens": int(raw.get("output_tokens", 0) or 0),
        "cost_provider_credits": float(raw.get("cost_provider_credits", 0.0) or 0.0),
        "wall_seconds": float(raw.get("wall_seconds", 0.0) or 0.0),
        "run_id": raw.get("run_id"),
        "agent_id": raw.get("agent_id") or raw.get("run_id"),
        "launch_hash": raw.get("launch_hash"),
        "error_type": raw.get("error_type"),
        "error_message": str(raw.get("error_message", ""))[:500],
    }


def _update_costs(manifest: dict[str, Any], output: pathlib.Path) -> None:
    evaluation_cost = 0.0
    for condition in EVALUATION_CONDITIONS:
        for test_id in manifest["evaluations"][condition]["tests"]:
            name = test_id.rsplit("/", 1)[-1]
            result_path = output / "evaluations" / condition / "runs" / name / "result.json"
            if result_path.is_file():
                result = _read_json_object(result_path, "hidden result")
                evaluation_cost += float(result.get("cost_provider_credits", 0.0) or 0.0)
    manifest["costs"]["evaluation_provider_credits"] = round(evaluation_cost, 6)
    manifest["costs"]["total_provider_credits"] = round(
        manifest["costs"]["campaign_provider_credits"]
        + manifest["costs"]["confirmation_provider_credits"]
        + manifest["costs"]["repair_provider_credits"]
        + manifest["costs"]["revision_provider_credits"]
        + evaluation_cost,
        6,
    )


def _validate_hidden_identity(hidden: HiddenScenarioIdentity) -> tuple[str, ...]:
    expected_ids = tuple(
        f"{hidden.scenario_id}/t{number}" for number in range(1, 11)
    )
    if len(hidden.tests) != 10 or tuple(test.test_id for test in hidden.tests) != expected_ids:
        raise ManifestMismatchError("hidden evaluator requires exactly stable tests t1..t10")
    for test in hidden.tests:
        for value, label in (
            (test.contract_identity, "test contract identity"),
            (test.candidate_hash, "candidate hash"),
            (test.dockerfile_hash, "Dockerfile hash"),
            (test.checks_hash, "checks hash"),
            (test.validation_evidence_hash, "validation evidence hash"),
        ):
            _require_digest(value, label)
        if (
            not test.criterion_ids
            or len(set(test.criterion_ids)) != len(test.criterion_ids)
            or any(
                not isinstance(identifier, str) or not identifier
                for identifier in test.criterion_ids
            )
        ):
            raise ManifestMismatchError(
                f"{test.test_id} criterion IDs must be non-empty and unique"
            )
        _require_image_digest(
            test.validation_image_digest,
            f"{test.test_id} validation image digest",
        )
    _require_digest(hidden.contract_identity, "scenario contract identity")
    _require_digest(hidden.base_skill_hash, "scenario base skill hash")
    return expected_ids


def _verify_start(
    start: Mapping[str, Any],
    *,
    execution_id: str,
    request_hash: str,
    test: HiddenCaseIdentity,
    skill_hash: str,
    model_config: Mapping[str, Any],
) -> None:
    expected = {
        "schema": "skillrace-hidden-start/1",
        "execution_id": execution_id,
        "request_hash": request_hash,
        "test_id": test.test_id,
        "test_contract_identity": test.contract_identity,
        "validation_evidence_hash": test.validation_evidence_hash,
        "validation_image_digest": test.validation_image_digest,
        "criterion_ids": list(test.criterion_ids),
        "skill_hash": skill_hash,
        "model_config": dict(model_config),
    }
    if start != expected:
        raise ManifestMismatchError(f"hidden start identity mismatch for {test.test_id}")


def _verify_receipt(
    receipt: Mapping[str, Any],
    *,
    execution_id: str,
    request_hash: str,
    start_hash: str,
    result_hash: str,
    test: HiddenCaseIdentity,
    raw_artifacts_hash: str,
) -> None:
    recovered = receipt.get("recovered_from_committed_result")
    if not isinstance(recovered, bool):
        raise ManifestMismatchError("hidden receipt recovery state is malformed")
    expected = _receipt_payload(
        execution_id,
        request_hash,
        start_hash,
        result_hash,
        test.validation_image_digest,
        test.criterion_ids,
        raw_artifacts_hash,
        recovered=recovered,
    )
    if receipt != expected:
        raise ManifestMismatchError(f"hidden result/receipt hash mismatch for {test.test_id}")


def verify_rq3_evaluation_artifacts(
    manifest_path: str | pathlib.Path,
    *,
    scenario_dir: str | pathlib.Path,
    require_complete: bool = True,
) -> dict[str, Any]:
    """Recursively verify the frozen 4x10 hidden-evaluation evidence graph.

    The verifier reloads the current hidden contracts and validation evidence,
    rehashes every raw execution artifact, and recomputes every grade.  A caller
    may set ``require_complete=False`` only to analyze an explicitly scheduled
    missing execution conservatively; the exact 40-cell schedule is still required.
    """

    path = pathlib.Path(manifest_path)
    output = path.parent
    manifest = load_rq3_manifest(path)
    hidden = _load_hidden_scenario_identity(scenario_dir)
    expected_test_ids = _validate_hidden_identity(hidden)
    if manifest.get("scenario_id") != hidden.scenario_id:
        raise ManifestMismatchError("RQ3/current hidden scenario identity mismatch")
    if manifest.get("scenario_contract_identity") != hidden.contract_identity:
        raise ManifestMismatchError("RQ3/current scenario contract identity mismatch")
    if manifest.get("scenario_base_skill_hash") != hidden.base_skill_hash:
        raise ManifestMismatchError("RQ3/current scenario base skill hash mismatch")
    evaluations = manifest.get("evaluations")
    if not isinstance(evaluations, Mapping) or tuple(evaluations) != EVALUATION_CONDITIONS:
        raise ManifestMismatchError("RQ3 evaluation must contain exactly four conditions")
    model_config = manifest.get("model_config")
    if not isinstance(model_config, Mapping):
        raise ManifestMismatchError("RQ3 model configuration is malformed")
    public_inputs = {
        "base_skill": manifest.get("base_skill"),
        "campaigns": manifest.get("campaigns"),
        "repairs": manifest.get("repairs"),
        "envelopes": manifest.get("feedback_envelopes"),
        "revisions": manifest.get("revisions"),
    }
    if any(not isinstance(value, Mapping) for value in public_inputs.values()):
        raise ManifestMismatchError("RQ3 public input links are malformed")
    _validate_inputs(
        str(manifest.get("protocol_hash")),
        public_inputs["base_skill"],
        public_inputs["campaigns"],
        public_inputs["envelopes"],
        public_inputs["revisions"],
        {condition: pathlib.Path(".") for condition in EVALUATION_CONDITIONS},
        model_config,
    )
    repairs = _normalized_repair_records(
        public_inputs["repairs"], public_inputs["campaigns"]
    )
    protocol_hash = _require_digest(manifest.get("protocol_hash"), "protocol hash")
    replication = manifest.get("replication")
    if not isinstance(replication, int) or replication <= 0:
        raise ManifestMismatchError("RQ3 replication identity is malformed")
    tests_by_id = {test.test_id: test for test in hidden.tests}

    evaluation_cost = 0.0
    for condition in EVALUATION_CONDITIONS:
        condition_row = evaluations[condition]
        if not isinstance(condition_row, Mapping):
            raise ManifestMismatchError(f"{condition} evaluation record is malformed")
        skill_hash = _require_digest(
            condition_row.get("skill_hash"), f"{condition} skill hash"
        )
        links = condition_row.get("tests")
        if not isinstance(links, Mapping) or tuple(links) != expected_test_ids:
            raise ManifestMismatchError(
                f"{condition} hidden test set must be exactly stable t1..t10"
            )
        summary_rows: list[dict[str, Any]] = []
        for test_id in expected_test_ids:
            test = tests_by_id[test_id]
            link = links[test_id]
            if not isinstance(link, Mapping):
                raise ManifestMismatchError(f"{condition}/{test_id} link is malformed")
            execution_id, request_hash = _execution_identity(
                protocol_hash,
                hidden.scenario_id,
                replication,
                condition,
                test,
                skill_hash,
                model_config,
            )
            expected_link_identity = {
                "test_contract_identity": test.contract_identity,
                "candidate_hash": test.candidate_hash,
                "dockerfile_hash": test.dockerfile_hash,
                "checks_hash": test.checks_hash,
                "criterion_ids": list(test.criterion_ids),
                "validation_evidence_hash": test.validation_evidence_hash,
                "validation_image_digest": test.validation_image_digest,
                "request_hash": request_hash,
                "execution_id": execution_id,
            }
            for field, expected in expected_link_identity.items():
                if link.get(field) != expected:
                    raise ManifestMismatchError(
                        f"{condition}/{test_id} identity mismatch for {field}"
                    )
            name = test_id.rsplit("/", 1)[-1]
            run_root = output / "evaluations" / condition / "runs" / name
            start_path = run_root / "start.json"
            result_path = run_root / "result.json"
            receipt_path = run_root / "receipt.json"
            execution_count = link.get("execution_count")
            if execution_count == 0:
                if require_complete:
                    raise ManifestMismatchError(
                        f"missing hidden execution for {condition}/{test_id}"
                    )
                if (
                    link.get("status") != "pending"
                    or any(
                        link.get(field) is not None
                        for field in (
                            "start_hash",
                            "result_hash",
                            "receipt_hash",
                            "raw_artifacts_hash",
                            "grade",
                        )
                    )
                    or start_path.exists()
                    or result_path.exists()
                    or receipt_path.exists()
                ):
                    raise ManifestMismatchError(
                        f"partial hidden execution state for {condition}/{test_id}"
                    )
                summary_rows.append(
                    {
                        "status": "missing",
                        "functional_pass": None,
                        "strict_pass": None,
                    }
                )
                continue
            if execution_count != 1:
                raise ManifestMismatchError(
                    f"{condition}/{test_id} must have exactly one execution"
                )
            for artifact_path, label in (
                (start_path, "hidden start"),
                (result_path, "hidden result"),
                (receipt_path, "hidden receipt"),
            ):
                if artifact_path.is_symlink() or not artifact_path.is_file():
                    raise ManifestMismatchError(
                        f"missing or unsafe {label} for {condition}/{test_id}"
                    )
            start_hash = file_hash(start_path)
            result_hash = file_hash(result_path)
            receipt_hash = file_hash(receipt_path)
            if (
                link.get("start_hash") != start_hash
                or link.get("result_hash") != result_hash
                or link.get("receipt_hash") != receipt_hash
            ):
                raise ManifestMismatchError(
                    f"recursive hidden artifact hash mismatch for {condition}/{test_id}"
                )
            start = _read_json_object(start_path, "hidden start")
            _verify_start(
                start,
                execution_id=execution_id,
                request_hash=request_hash,
                test=test,
                skill_hash=skill_hash,
                model_config=model_config,
            )
            result = _read_json_object(result_path, "hidden result")
            if result.get("start_hash") != start_hash:
                raise ManifestMismatchError(
                    f"hidden result/start hash mismatch for {condition}/{test_id}"
                )
            grade = _validate_result(
                result,
                execution_id,
                request_hash,
                test=test,
                run_root=run_root,
            )
            raw_hash = _require_digest(
                result.get("raw_artifacts_hash"), "raw artifacts hash"
            )
            receipt = _read_json_object(receipt_path, "hidden receipt")
            _verify_receipt(
                receipt,
                execution_id=execution_id,
                request_hash=request_hash,
                start_hash=start_hash,
                result_hash=result_hash,
                test=test,
                raw_artifacts_hash=raw_hash,
            )
            if (
                link.get("status") != result.get("status")
                or link.get("grade") != grade
                or link.get("raw_artifacts_hash") != raw_hash
                or link.get("agent_id")
                != (result.get("agent_id") or result.get("run_id"))
            ):
                raise ManifestMismatchError(
                    f"hidden result/manifest grade mismatch for {condition}/{test_id}"
                )
            evaluation_cost += float(result.get("cost_provider_credits", 0.0) or 0.0)
            summary_rows.append(
                {
                    "status": result["status"],
                    "functional_pass": grade["functional_pass"],
                    "strict_pass": grade["strict_pass"],
                }
            )
        recomputed_summary = summarize_runs(summary_rows)
        recorded_summary = condition_row.get("summary")
        if recorded_summary is not None and recorded_summary != recomputed_summary:
            raise ManifestMismatchError(f"{condition} aggregate grade mismatch")
        if require_complete and recorded_summary != recomputed_summary:
            raise ManifestMismatchError(f"{condition} aggregate grade is missing")

    costs = manifest.get("costs")
    if not isinstance(costs, Mapping):
        raise ManifestMismatchError("RQ3 cost record is malformed")
    campaign_cost = round(
        sum(
            float(manifest["campaigns"][name].get("cost_provider_credits", 0.0) or 0.0)
            for name in PRODUCERS
        ),
        6,
    )
    confirmation_cost = round(
        sum(
            float(
                manifest["feedback_envelopes"][name].get(
                    "confirmation_cost_provider_credits", 0.0
                )
                or 0.0
            )
            for name in PRODUCERS
        ),
        6,
    )
    revision_cost = round(
        sum(
            float(manifest["revisions"][name].get("cost_provider_credits", 0.0) or 0.0)
            for name in PRODUCERS
        ),
        6,
    )
    repair_cost = round(
        sum(
            float(repairs[name].get("costs", {}).get("total_provider_credits", 0.0) or 0.0)
            for name in PRODUCERS
        ),
        6,
    )
    evaluation_cost = round(evaluation_cost, 6)
    expected_costs = {
        "campaign_provider_credits": campaign_cost,
        "confirmation_provider_credits": confirmation_cost,
        "repair_provider_credits": repair_cost,
        "revision_provider_credits": revision_cost,
        "evaluation_provider_credits": evaluation_cost,
        "total_provider_credits": round(
            campaign_cost
            + confirmation_cost
            + repair_cost
            + revision_cost
            + evaluation_cost,
            6,
        ),
    }
    if dict(costs) != expected_costs:
        raise ManifestMismatchError("RQ3 evaluation cost mismatch")
    return manifest


def evaluate_hidden_scenario(
    *,
    scenario_dir: str | pathlib.Path,
    out_dir: str | pathlib.Path,
    protocol_hash: str,
    replication: int,
    base_skill: Mapping[str, Any],
    campaigns: Mapping[str, Mapping[str, Any]],
    envelopes: Mapping[str, Mapping[str, Any]],
    revisions: Mapping[str, Mapping[str, Any]],
    skills_by_condition: Mapping[str, pathlib.Path],
    model_config: Mapping[str, Any],
    public_artifact_roots: Sequence[str | pathlib.Path],
    repairs: Mapping[str, Mapping[str, Any]] | None = None,
    executor: Callable[[HiddenExecutionRequest], Mapping[str, Any]] = execute_hidden_request,
) -> dict[str, Any]:
    """Run exactly four conditions once/test with atomic, receipt-based resumption.

    This is the first orchestration function that resolves ``scenario/tests``.
    Campaign production, envelope construction, and revision operate on the staged
    public directory and never call this loader.
    """

    if not isinstance(replication, int) or replication <= 0:
        raise ManifestMismatchError("replication must be a positive integer")
    _validate_inputs(
        protocol_hash,
        base_skill,
        campaigns,
        envelopes,
        revisions,
        skills_by_condition,
        model_config,
    )
    normalized_repairs = _normalized_repair_records(repairs, campaigns)
    hidden = _load_hidden_scenario_identity(scenario_dir)
    if len(hidden.tests) != 10 or tuple(test.test_id for test in hidden.tests) != tuple(
        f"{hidden.scenario_id}/t{number}" for number in range(1, 11)
    ):
        raise ManifestMismatchError("hidden evaluator requires exactly stable tests t1..t10")
    for test in hidden.tests:
        for value, label in (
            (test.contract_identity, "test contract identity"),
            (test.candidate_hash, "candidate hash"),
            (test.dockerfile_hash, "Dockerfile hash"),
            (test.checks_hash, "checks hash"),
            (test.validation_evidence_hash, "validation evidence hash"),
        ):
            _require_digest(value, label)
        if (
            not test.criterion_ids
            or len(set(test.criterion_ids)) != len(test.criterion_ids)
            or any(not identifier for identifier in test.criterion_ids)
        ):
            raise ManifestMismatchError(
                f"{test.test_id} criterion IDs must be non-empty and unique"
            )
        _require_image_digest(
            test.validation_image_digest,
            f"{test.test_id} validation image digest",
        )
    _require_digest(hidden.contract_identity, "scenario contract identity")
    _require_digest(hidden.base_skill_hash, "scenario base skill hash")
    if hidden.base_skill_hash != base_skill.get("skill_hash"):
        raise ManifestMismatchError(
            "zero-shot base skill hash differs from the frozen scenario contract"
        )
    if not public_artifact_roots:
        raise LeakageError("public artifact roots are required for the hidden sentinel audit")
    hidden_root = hidden.tests[0].root.parent
    assert_no_hidden_material(hidden_root, public_artifact_roots)
    paths = {condition: pathlib.Path(path) for condition, path in skills_by_condition.items()}
    hashes = _skill_hashes(base_skill, revisions, paths)
    expected = _new_manifest(
        hidden,
        protocol_hash,
        replication,
        base_skill,
        campaigns,
        normalized_repairs,
        envelopes,
        revisions,
        hashes,
        model_config,
    )
    output = pathlib.Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "rq3-manifest.json"
    if manifest_path.exists():
        manifest = load_rq3_manifest(
            manifest_path, expected_protocol_hash=protocol_hash
        )
        _assert_resume_identity(manifest, expected)
    else:
        manifest = expected
        _write_manifest(manifest_path, manifest)

    tests_by_id = {test.test_id: test for test in hidden.tests}
    for condition in EVALUATION_CONDITIONS:
        for test_id, manifest_row in manifest["evaluations"][condition]["tests"].items():
            test = tests_by_id[test_id]
            test_name = test_id.rsplit("/", 1)[-1]
            run_root = output / "evaluations" / condition / "runs" / test_name
            start_path = run_root / "start.json"
            result_path = run_root / "result.json"
            receipt_path = run_root / "receipt.json"
            execution_id = manifest_row["execution_id"]
            request_hash = manifest_row["request_hash"]
            if (
                manifest_row.get("execution_count") == 1
                and not result_path.exists()
            ):
                raise ManifestMismatchError(
                    f"committed hidden execution artifacts are missing for {condition}/{test_id}"
                )
            if receipt_path.exists() and not result_path.exists():
                raise ManifestMismatchError(
                    f"receipt exists without committed result for {condition}/{test_id}"
                )
            if result_path.exists():
                if not start_path.is_file() or start_path.is_symlink():
                    raise ManifestMismatchError(
                        f"hidden start record is missing for {condition}/{test_id}"
                    )
                start = _read_json_object(start_path, "hidden start")
                _verify_start(
                    start,
                    execution_id=execution_id,
                    request_hash=request_hash,
                    test=test,
                    skill_hash=hashes[condition],
                    model_config=model_config,
                )
                start_hash = file_hash(start_path)
                result = _read_json_object(result_path, "hidden result")
                if result.get("start_hash") != start_hash:
                    raise ManifestMismatchError(
                        f"hidden result/start mismatch for {condition}/{test_id}"
                    )
                recomputed_grade = _validate_result(
                    result,
                    execution_id,
                    request_hash,
                    test=test,
                    run_root=run_root,
                )
                current_result_hash = file_hash(result_path)
                if manifest_row.get("result_hash") not in {None, current_result_hash}:
                    raise ManifestMismatchError(
                        f"result hash mismatch for {condition}/{test_id}"
                    )
                recovered = False
                if receipt_path.exists():
                    receipt = _read_json_object(receipt_path, "hidden receipt")
                    _verify_receipt(
                        receipt,
                        execution_id=execution_id,
                        request_hash=request_hash,
                        start_hash=start_hash,
                        result_hash=current_result_hash,
                        test=test,
                        raw_artifacts_hash=str(result["raw_artifacts_hash"]),
                    )
                else:
                    recovered = True
                    run_root.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(
                        receipt_path,
                        _receipt_payload(
                            execution_id,
                            request_hash,
                            start_hash,
                            current_result_hash,
                            test.validation_image_digest,
                            test.criterion_ids,
                            str(result.get("raw_artifacts_hash")),
                            recovered=True,
                        ),
                    )
                manifest_row.update(
                    {
                        "execution_count": 1,
                        "status": result["status"],
                        "start_hash": start_hash,
                        "result_hash": current_result_hash,
                        "receipt_hash": file_hash(receipt_path),
                        "raw_artifacts_hash": result["raw_artifacts_hash"],
                        "agent_id": result.get("agent_id") or result.get("run_id"),
                        "grade": copy.deepcopy(recomputed_grade),
                    }
                )
                if recovered:
                    _update_costs(manifest, output)
                    _write_manifest(manifest_path, manifest)
                continue

            if start_path.exists():
                start = _read_json_object(start_path, "hidden start")
                _verify_start(
                    start,
                    execution_id=execution_id,
                    request_hash=request_hash,
                    test=test,
                    skill_hash=hashes[condition],
                    model_config=model_config,
                )
                raise UncertainExternalOutcomeError(
                    f"external hidden execution outcome is unknown for {condition}/{test_id}; "
                    "the durable start exists without a terminal result"
                )

            request = HiddenExecutionRequest(
                test_id=test_id,
                hidden_case_dir=test.root,
                skill_name=hidden.scenario_id,
                skill_dir=paths[condition],
                run_dir=run_root / "agent",
                agent_model=str(model_config["model"]),
                wall_clock=int(model_config["wall_clock"]),
                contract_identity=test.contract_identity,
                criterion_ids=test.criterion_ids,
                validation_image_digest=test.validation_image_digest,
            )
            run_root.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                start_path,
                {
                    "schema": "skillrace-hidden-start/1",
                    "execution_id": execution_id,
                    "request_hash": request_hash,
                    "test_id": test_id,
                    "test_contract_identity": test.contract_identity,
                    "validation_evidence_hash": test.validation_evidence_hash,
                    "validation_image_digest": test.validation_image_digest,
                    "criterion_ids": list(test.criterion_ids),
                    "skill_hash": hashes[condition],
                    "model_config": copy.deepcopy(dict(model_config)),
                },
            )
            start_hash = file_hash(start_path)
            try:
                raw_result: Any = executor(request)
            except TimeoutError as error:
                raw_result = {
                    "status": "timeout",
                    "verdicts": [],
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                }
            except Exception as error:  # noqa: BLE001 - preserve one-shot executor failure
                raw_result = {
                    "status": "error",
                    "verdicts": [],
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                }
            normalized = _normalize_executor_result(raw_result)
            raw_artifacts = raw_execution_artifacts(
                run_root / "agent" / "execution",
                path_prefix="agent/execution",
            )
            if normalized["status"] == "completed" and any(
                record["sha256"] is None for record in raw_artifacts.values()
            ):
                normalized["status"] = "inconclusive"
                normalized["error_type"] = "MissingRawExecutionArtifact"
                normalized["error_message"] = (
                    "completed executor result lacked launch/run/verdict/cost evidence"
                )
            raw_artifacts_hash = canonical_json_hash(raw_artifacts)
            grade = grade_run(
                normalized["verdicts"],
                execution_status=normalized["status"],
                expected_criterion_ids=test.criterion_ids,
            )
            result = {
                "schema": "skillrace-hidden-result/1",
                "execution_id": execution_id,
                "request_hash": request_hash,
                "start_hash": start_hash,
                "test_id": test_id,
                "test_contract_identity": test.contract_identity,
                "validation_evidence_hash": test.validation_evidence_hash,
                "validation_image_digest": test.validation_image_digest,
                "criterion_ids": list(test.criterion_ids),
                "execution_status": normalized["status"],
                "status": grade["status"],
                "grade": grade,
                "verdicts": normalized["verdicts"],
                "raw_artifacts": raw_artifacts,
                "raw_artifacts_hash": raw_artifacts_hash,
                "input_tokens": normalized["input_tokens"],
                "output_tokens": normalized["output_tokens"],
                "cost_provider_credits": normalized["cost_provider_credits"],
                "wall_seconds": normalized["wall_seconds"],
                "run_id": normalized["run_id"],
                "agent_id": normalized["agent_id"],
                "launch_hash": raw_artifacts["launch"]["sha256"],
                "error_type": normalized["error_type"],
                "error_message": normalized["error_message"],
            }
            _reject_secret_fields(result, "hidden result")
            atomic_write_json(result_path, result)
            result_hash = file_hash(result_path)
            atomic_write_json(
                receipt_path,
                _receipt_payload(
                    execution_id,
                    request_hash,
                    start_hash,
                    result_hash,
                    test.validation_image_digest,
                    test.criterion_ids,
                    raw_artifacts_hash,
                    recovered=False,
                ),
            )
            manifest_row.update(
                {
                    "execution_count": 1,
                    "status": result["status"],
                    "start_hash": start_hash,
                    "result_hash": result_hash,
                    "receipt_hash": file_hash(receipt_path),
                    "raw_artifacts_hash": raw_artifacts_hash,
                    "agent_id": result.get("agent_id") or result.get("run_id"),
                    "grade": copy.deepcopy(grade),
                }
            )
            _update_costs(manifest, output)
            _write_manifest(manifest_path, manifest)
    for condition in EVALUATION_CONDITIONS:
        summary_rows = []
        for row in manifest["evaluations"][condition]["tests"].values():
            grade = row.get("grade") if isinstance(row.get("grade"), Mapping) else {}
            summary_rows.append(
                {
                    "status": row.get("status", "missing"),
                    "functional_pass": grade.get("functional_pass"),
                    "strict_pass": grade.get("strict_pass"),
                }
            )
        manifest["evaluations"][condition]["summary"] = summarize_runs(summary_rows)
    _update_costs(manifest, output)
    _write_manifest(manifest_path, manifest)
    return verify_rq3_evaluation_artifacts(
        manifest_path,
        scenario_dir=scenario_dir,
        require_complete=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Lean RQ3 staging and manifest verification")
    subparsers = parser.add_subparsers(dest="command", required=True)
    stage = subparsers.add_parser("stage", help="create a public-only scenario stage")
    stage.add_argument("--scenario", required=True)
    stage.add_argument("--out", required=True)
    verify = subparsers.add_parser("verify", help="verify an RQ3 manifest hash")
    verify.add_argument("manifest")
    args = parser.parse_args()
    if args.command == "stage":
        print(stage_public_scenario(args.scenario, args.out))
    else:
        value = load_rq3_manifest(args.manifest)
        print(f"verified {value['rq3_id']}")


if __name__ == "__main__":
    main()
