"""Strict adapter from the shared campaign/2 engine to RQ3 provenance records."""

from __future__ import annotations

import json
import math
import pathlib
import re
from collections.abc import Mapping
from typing import Any

from .io_utils import atomic_write_json, canonical_json_hash, file_hash


class CampaignArtifactError(ValueError):
    """A campaign or one of its immutable linked records failed verification."""


_ATTEMPT_RE = re.compile(r"e(?P<execution>[0-9]{4})-a(?P<attempt>[0-9]{2})\Z")
_LIFECYCLE_FILES = {
    "started": "external.started.json",
    "external-terminal": "external.terminal.json",
    "executor-terminal": "executor.terminal.json",
}


def _read(path: pathlib.Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise CampaignArtifactError(f"{label} symlink is forbidden: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CampaignArtifactError(f"cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise CampaignArtifactError(f"{label} must be a JSON object: {path}")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise CampaignArtifactError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _cost(value: Any, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise CampaignArtifactError(f"{label} must be finite and non-negative")
    return float(value)


def _write_or_verify(path: pathlib.Path, value: Mapping[str, Any], label: str) -> None:
    if path.exists():
        if _read(path, label) != value:
            raise CampaignArtifactError(f"immutable {label} mismatch: {path}")
    else:
        atomic_write_json(path, dict(value))


def prepare_campaign_input_record(
    campaign_dir: str | pathlib.Path,
    *,
    method: str,
    protocol_hash: str,
    base_skill_hash: str,
    base_package_hash: str,
    public_stage_hash: str,
    output_identity: str,
) -> pathlib.Path:
    """Publish the public, pre-execution identity omitted by the generic engine."""

    if method not in {"random", "greybox", "skillrace"}:
        raise CampaignArtifactError("invalid RQ3 campaign method")
    root = pathlib.Path(campaign_dir)
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise CampaignArtifactError("campaign directory must be regular")
    root.mkdir(parents=True, exist_ok=True)
    value = {
        "schema": "skillrace-rq3-campaign-input/1",
        "method": method,
        "protocol_hash": _digest(protocol_hash, "protocol_hash"),
        "base_skill_hash": _digest(base_skill_hash, "base_skill_hash"),
        "base_package_hash": _digest(base_package_hash, "base_package_hash"),
        "public_stage_hash": _digest(public_stage_hash, "public_stage_hash"),
        "output_identity": _digest(output_identity, "output_identity"),
    }
    path = root / "rq3-input.json"
    _write_or_verify(path, value, "RQ3 campaign input")
    return path


def _safe_run_file(root: pathlib.Path, run: Any, name: str) -> pathlib.Path | None:
    if not isinstance(run, str) or not run:
        return None
    raw = pathlib.Path(run)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if path != root and root not in path.parents:
        raise CampaignArtifactError(f"campaign run path escapes artifact root: {run}")
    target = path / name
    return target if target.is_file() and not target.is_symlink() else None


def _generation_cost(state: Mapping[str, Any]) -> float:
    main = state.get("generator_state")
    bootstrap = state.get("bootstrap_generator_state")
    main = main if isinstance(main, Mapping) else {}
    bootstrap = bootstrap if isinstance(bootstrap, Mapping) else {}
    main_cost = _cost(main.get("gen_cost_usd", 0.0) or 0.0, "generator cost")
    # SkillRACE's snapshot already includes its owned seed generator cost.  The
    # engine also stores that same seed object as bootstrap state, so do not count it twice.
    if main.get("schema") == "skillrace-generator/1":
        return main_cost
    return main_cost + _cost(
        bootstrap.get("gen_cost_usd", 0.0) or 0.0, "bootstrap generator cost"
    )


def derive_campaign_cost_record(
    campaign_path: str | pathlib.Path,
) -> dict[str, Any]:
    """Derive a frozen cost sidecar from committed result/run receipts."""

    path = pathlib.Path(campaign_path)
    state = _read(path, "campaign")
    root = path.parent.resolve()
    attempts = state.get("attempts")
    if not isinstance(attempts, list):
        raise CampaignArtifactError("campaign attempts are malformed")
    compile_cost = 0.0
    agent_cost = 0.0
    input_tokens = output_tokens = 0
    receipts: list[dict[str, Any]] = []
    agent_ids: list[str] = []
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            raise CampaignArtifactError("campaign attempt is malformed")
        result = attempt.get("result")
        result = result if isinstance(result, Mapping) else {}
        compile_cost += _cost(
            result.get("compile_cost_usd", attempt.get("compile_cost_usd", 0.0)) or 0.0,
            "compile cost",
        )
        if attempt.get("consume_budget") is not True:
            continue
        run = attempt.get("run") or result.get("run_dir")
        cost_path = _safe_run_file(root, run, "cost.json")
        run_path = _safe_run_file(root, run, "run.json")
        if cost_path is not None:
            raw_cost = _read(cost_path, "agent cost receipt")
            usd = raw_cost.get("usd", raw_cost.get("price_usd", raw_cost.get("cost_usd", 0.0)))
            incoming = raw_cost.get("in", raw_cost.get("input_tokens", 0))
            outgoing = raw_cost.get("out", raw_cost.get("output_tokens", 0))
            receipt_link = {
                "attempt_id": attempt.get("attempt_id"),
                "path": cost_path.relative_to(root).as_posix(),
                "file_hash": file_hash(cost_path),
            }
        else:
            usd = result.get("agent_cost_usd", result.get("cost_usd"))
            incoming = result.get("input_tokens")
            outgoing = result.get("output_tokens")
            if usd is None or incoming is None or outgoing is None:
                raise CampaignArtifactError(
                    f"counted attempt {attempt.get('attempt_id')} lacks an agent cost receipt"
                )
            receipt_link = {
                "attempt_id": attempt.get("attempt_id"),
                "path": None,
                "file_hash": None,
                "embedded_result_hash": canonical_json_hash(result),
            }
        agent_cost += _cost(usd, "agent cost")
        if not isinstance(incoming, int) or incoming < 0 or not isinstance(outgoing, int) or outgoing < 0:
            raise CampaignArtifactError("agent token counts must be non-negative integers")
        input_tokens += incoming
        output_tokens += outgoing
        agent_id = result.get("agent_id") or result.get("run_id")
        if agent_id is None and run_path is not None:
            agent_id = _read(run_path, "agent run manifest").get("run_id")
        if not isinstance(agent_id, str) or not agent_id:
            raise CampaignArtifactError(
                f"counted attempt {attempt.get('attempt_id')} lacks an agent ID"
            )
        agent_ids.append(agent_id)
        receipt_link["agent_id"] = agent_id
        receipts.append(receipt_link)
    generation_cost = _generation_cost(state)
    value = {
        "schema": "skillrace-rq3-campaign-costs/1",
        "source_campaign_hash": canonical_json_hash(state),
        "generation_usd": round(generation_cost, 6),
        "compile_usd": round(compile_cost, 6),
        "agent_usd": round(agent_cost, 6),
        "total_usd": round(generation_cost + compile_cost + agent_cost, 6),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "agent_ids": agent_ids,
        "agent_cost_receipts": receipts,
        "confirmation_usd": 0.0,
        "confirmation_executions": 0,
    }
    output = path.parent / "rq3-costs.json"
    _write_or_verify(output, value, "RQ3 campaign costs")
    return value


def _verify_envelope(
    value: Mapping[str, Any],
    *,
    state: Mapping[str, Any],
    execution_id: str,
    attempt_id: str,
    label: str,
) -> None:
    expected = {
        "protocol_hash": state["protocol_hash"],
        "method": state["method"],
        "skill": state["skill"],
        "output_identity": state["output_identity"],
        "execution_id": execution_id,
        "attempt_id": attempt_id,
    }
    for field, wanted in expected.items():
        if value.get(field) != wanted:
            raise CampaignArtifactError(f"{label} identity mismatch for {field}")


def _verify_attempt_artifacts(root: pathlib.Path, state: Mapping[str, Any], record: Mapping[str, Any]) -> None:
    attempt_id = record["attempt_id"]
    execution_id = record["execution_id"]
    directory = root / "attempts" / attempt_id
    if directory.is_symlink() or not directory.is_dir():
        raise CampaignArtifactError(f"attempt artifact directory is missing: {attempt_id}")
    artifacts = {
        "proposal": ("proposal.json", "proposal_hash"),
        "receipt": ("receipt.json", "receipt_hash"),
        "cleanup intent": ("cleanup.intent.json", "cleanup_intent_hash"),
        "cleanup": ("cleanup.json", "cleanup_hash"),
    }
    if record.get("consume_budget") is True:
        artifacts["fold"] = ("fold.json", "fold_hash")
    loaded: dict[str, Mapping[str, Any]] = {}
    for label, (filename, hash_field) in artifacts.items():
        value = _read(directory / filename, label)
        loaded[label] = value
        if canonical_json_hash(value) != record.get(hash_field):
            raise CampaignArtifactError(f"{label} hash mismatch for {attempt_id}")
    _verify_envelope(
        loaded["proposal"], state=state, execution_id=execution_id,
        attempt_id=attempt_id, label="proposal",
    )
    _verify_envelope(
        loaded["receipt"], state=state, execution_id=execution_id,
        attempt_id=attempt_id, label="receipt",
    )
    proposal_candidate = loaded["proposal"].get("candidate")
    if not isinstance(proposal_candidate, Mapping):
        raise CampaignArtifactError(f"proposal candidate is malformed for {attempt_id}")
    if proposal_candidate.get("candidate_id") != record.get("candidate_id"):
        raise CampaignArtifactError(f"proposal/campaign candidate identity mismatch for {attempt_id}")
    proposal_provenance = proposal_candidate.get("provenance")
    if not isinstance(proposal_provenance, Mapping) or dict(proposal_provenance) != dict(
        record.get("provenance") or {}
    ):
        raise CampaignArtifactError(f"proposal/campaign provenance mismatch for {attempt_id}")
    if loaded["proposal"].get("phase") != record.get("phase"):
        raise CampaignArtifactError(f"proposal/campaign phase mismatch for {attempt_id}")
    if loaded["receipt"].get("candidate_id") != record.get("candidate_id"):
        raise CampaignArtifactError(f"receipt/campaign candidate identity mismatch for {attempt_id}")
    receipt_result = loaded["receipt"].get("result")
    if not isinstance(receipt_result, Mapping) or dict(receipt_result) != dict(
        record.get("result") or {}
    ):
        raise CampaignArtifactError(f"receipt/campaign result mismatch for {attempt_id}")
    if record.get("consume_budget") is True and loaded["fold"].get("phase") != record.get("phase"):
        raise CampaignArtifactError(f"fold/campaign phase mismatch for {attempt_id}")
    journal = loaded["receipt"].get("lifecycle_journal")
    if journal is not None:
        if not isinstance(journal, Mapping):
            raise CampaignArtifactError(f"receipt lifecycle journal is malformed for {attempt_id}")
        for event, expected_hash in journal.items():
            filename = _LIFECYCLE_FILES.get(str(event))
            if filename is None:
                raise CampaignArtifactError(f"unknown lifecycle event {event}")
            if canonical_json_hash(_read(directory / filename, f"{event} lifecycle")) != expected_hash:
                raise CampaignArtifactError(f"{event} lifecycle hash mismatch for {attempt_id}")


def validate_campaign_artifact(
    campaign_path: str | pathlib.Path,
    *,
    expected_method: str,
    expected_protocol_hash: str,
    expected_base_skill_hash: str,
) -> dict[str, Any]:
    """Recursively validate a complete generic campaign/2 as an RQ3 input."""

    path = pathlib.Path(campaign_path)
    state = _read(path, "campaign")
    if state.get("schema") != "campaign/2":
        raise CampaignArtifactError("RQ3 requires a campaign/2 artifact")
    protocol_hash = _digest(expected_protocol_hash, "expected_protocol_hash")
    base_hash = _digest(expected_base_skill_hash, "expected_base_skill_hash")
    embedded = state.get("protocol")
    if not isinstance(embedded, Mapping) or canonical_json_hash(embedded) != state.get("protocol_hash"):
        raise CampaignArtifactError("embedded protocol hash mismatch")
    if state.get("protocol_hash") != protocol_hash:
        raise CampaignArtifactError("campaign protocol hash mismatch")
    if state.get("method") != expected_method:
        raise CampaignArtifactError("campaign method mismatch")
    allocation = {
        "budget": 30,
        "bootstrap": 0 if expected_method == "random" else 10,
        "exploration": 30 if expected_method == "random" else 20,
    }
    if (
        state.get("budget") != 30
        or state.get("counted_executions") != 30
        or state.get("complete") is not True
        or state.get("status") != "completed"
        or state.get("allocation") != allocation
        or state.get("model") != "qwen3.6-flash"
        or state.get("agent_model") != "qwen3.6-flash"
    ):
        raise CampaignArtifactError("campaign does not satisfy the frozen complete allocation")
    inputs = _read(path.parent / "rq3-input.json", "RQ3 campaign input")
    if (
        inputs.get("schema") != "skillrace-rq3-campaign-input/1"
        or inputs.get("method") != expected_method
        or inputs.get("protocol_hash") != protocol_hash
        or inputs.get("base_skill_hash") != base_hash
        or inputs.get("output_identity") != state.get("output_identity")
    ):
        raise CampaignArtifactError("RQ3 campaign input identity mismatch")
    for field in ("base_package_hash", "public_stage_hash"):
        _digest(inputs.get(field), f"campaign input {field}")

    attempts = state.get("attempts")
    iterations = state.get("iterations")
    if not isinstance(attempts, list) or not isinstance(iterations, list):
        raise CampaignArtifactError("campaign attempts/iterations are malformed")
    execution = attempt_number = 0
    counted: list[Mapping[str, Any]] = []
    for raw in attempts:
        if not isinstance(raw, Mapping):
            raise CampaignArtifactError("campaign attempt is malformed")
        match = _ATTEMPT_RE.fullmatch(str(raw.get("attempt_id", "")))
        if (
            match is None
            or int(match.group("execution")) != execution
            or int(match.group("attempt")) != attempt_number
            or raw.get("execution_id") != f"e{execution:04d}"
        ):
            raise CampaignArtifactError("campaign attempt IDs are not contiguous")
        _verify_attempt_artifacts(path.parent.resolve(), state, raw)
        if raw.get("consume_budget") is True:
            counted.append(raw)
            execution += 1
            attempt_number = 0
        else:
            attempt_number += 1
            if attempt_number >= int(state.get("max_pre_agent_attempts", 0)):
                raise CampaignArtifactError("campaign attempt sequence exceeds the frozen cap")
    if execution != 30 or len(iterations) != 30:
        raise CampaignArtifactError("campaign counted execution sequence is incomplete")
    expected_phases = [
        "explore" if expected_method == "random" or ordinal >= 10 else "bootstrap"
        for ordinal in range(30)
    ]
    if (
        [row.get("execution_id") for row in iterations]
        != [f"e{ordinal:04d}" for ordinal in range(30)]
        or [row.get("phase") for row in iterations] != expected_phases
        or [row.get("attempt_id") for row in iterations]
        != [row.get("attempt_id") for row in counted]
    ):
        raise CampaignArtifactError("campaign phase sequence or contiguous iteration IDs mismatch")

    candidate_ids: list[str] = []
    root = path.parent.resolve()
    for ordinal, (attempt, iteration, phase) in enumerate(
        zip(counted, iterations, expected_phases, strict=True)
    ):
        result = attempt.get("result")
        result = result if isinstance(result, Mapping) else {}
        if attempt.get("agent_started") is not True or result.get("agent_started") is not True:
            raise CampaignArtifactError(
                f"counted execution e{ordinal:04d} lacks agent_started=True evidence"
            )
        candidate_id = attempt.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise CampaignArtifactError("counted execution lacks a candidate ID")
        candidate_ids.append(candidate_id)
        if iteration.get("candidate_id") != candidate_id:
            raise CampaignArtifactError("iteration/counted candidate identity mismatch")
        provenance = attempt.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        iteration_provenance = iteration.get("provenance")
        if not isinstance(iteration_provenance, Mapping) or dict(iteration_provenance) != dict(provenance):
            raise CampaignArtifactError("iteration/counted provenance mismatch")
        source = provenance.get("source")
        if phase == "bootstrap":
            if source != "bootstrap":
                raise CampaignArtifactError("adaptive bootstrap provenance must come from bootstrap")
        elif expected_method == "random":
            if source != "random" or provenance.get("independent_test") is not True:
                raise CampaignArtifactError(
                    "Random counted cases require independent fresh-test provenance"
                )
        elif expected_method == "greybox" and source != "greybox":
            raise CampaignArtifactError("Greybox exploration provenance mismatch")
        elif expected_method == "skillrace" and source not in {
            "skillrace",
            "skillrace-fallback",
        }:
            raise CampaignArtifactError("SkillRACE exploration provenance mismatch")

        run = attempt.get("run") or result.get("run_dir")
        run_path = _safe_run_file(root, run, "run.json")
        if run_path is None:
            raise CampaignArtifactError(
                f"counted execution {attempt.get('attempt_id')} lacks a raw run manifest"
            )
        run_manifest = _read(run_path, "agent run manifest")
        agent_id = result.get("agent_id") or result.get("run_id")
        if run_manifest.get("model") != "qwen3.6-flash":
            raise CampaignArtifactError("raw run model differs from frozen qwen3.6-flash")
        if run_manifest.get("agent_started") is not True:
            raise CampaignArtifactError("raw run manifest lacks agent_started=True")
        if run_manifest.get("run_id") != agent_id:
            raise CampaignArtifactError("raw run/receipt agent identity mismatch")

    if len(set(candidate_ids)) != 30:
        raise CampaignArtifactError("campaign candidate IDs must be unique")

    costs = _read(path.parent / "rq3-costs.json", "RQ3 campaign costs")
    if costs.get("source_campaign_hash") != canonical_json_hash(state):
        raise CampaignArtifactError("campaign cost record source hash mismatch")
    for field in ("generation_usd", "compile_usd", "agent_usd", "total_usd"):
        _cost(costs.get(field), f"campaign cost {field}")
    if round(costs["generation_usd"] + costs["compile_usd"] + costs["agent_usd"], 6) != costs["total_usd"]:
        raise CampaignArtifactError("campaign cost total mismatch")
    if costs.get("confirmation_usd") != 0.0 or costs.get("confirmation_executions") != 0:
        raise CampaignArtifactError("search campaign costs must exclude confirmation")
    agent_ids = costs.get("agent_ids")
    if not isinstance(agent_ids, list) or len(agent_ids) != 30 or any(
        not isinstance(value, str) or not value for value in agent_ids
    ):
        raise CampaignArtifactError("campaign cost record lacks 30 agent IDs")
    if len(set(agent_ids)) != 30:
        raise CampaignArtifactError("campaign cost record requires 30 unique agent IDs")
    cost_receipts = costs.get("agent_cost_receipts")
    if not isinstance(cost_receipts, list) or len(cost_receipts) != 30:
        raise CampaignArtifactError("campaign cost record lacks 30 agent cost receipts")
    for attempt, agent_id, receipt in zip(counted, agent_ids, cost_receipts, strict=True):
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("attempt_id") != attempt.get("attempt_id")
            or receipt.get("agent_id") != agent_id
            or not isinstance(receipt.get("path"), str)
            or not isinstance(receipt.get("file_hash"), str)
        ):
            raise CampaignArtifactError("agent cost receipt identity mismatch")
        cost_path = _safe_run_file(path.parent.resolve(), attempt.get("run"), "cost.json")
        if cost_path is None or cost_path.relative_to(path.parent.resolve()).as_posix() != receipt["path"]:
            raise CampaignArtifactError("agent cost receipt path mismatch")
        if file_hash(cost_path) != receipt["file_hash"]:
            raise CampaignArtifactError("agent cost receipt hash mismatch")
    return {
        "schema": "campaign/2",
        "artifact_hash": canonical_json_hash(state),
        "file_hash": file_hash(path),
        "input_record_hash": file_hash(path.parent / "rq3-input.json"),
        "cost_record_hash": file_hash(path.parent / "rq3-costs.json"),
        "protocol_hash": protocol_hash,
        "base_skill_hash": base_hash,
        "base_package_hash": inputs["base_package_hash"],
        "public_stage_hash": inputs["public_stage_hash"],
        "budget": 30,
        "counted_executions": 30,
        "complete": True,
        "model": "qwen3.6-flash",
        "agent_model": "qwen3.6-flash",
        "allocation": allocation,
        "cost_usd": costs["total_usd"],
        "input_tokens": costs.get("input_tokens", 0),
        "output_tokens": costs.get("output_tokens", 0),
        "agent_ids": list(agent_ids),
    }
