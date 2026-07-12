"""Strict, offline contracts for the held-out skill-generation benchmark.

The validator deliberately separates structural evidence from Docker evidence.  A
``pending-docker`` record proves only that the package is reviewable; it is never
treated as evidence that an oracle accepts its reference or rejects its negatives.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .io_utils import atomic_write_json, canonical_json_hash


SCHEMA_SCENARIO = "skillrace-scenario/1"
SCHEMA_TEST = "skillrace-hidden-test/1"
SCHEMA_EVIDENCE = "skillrace-oracle-evidence/1"
CANONICAL_SCENARIOS = (
    "argparse-cli",
    "config-parser",
    "csv-stats",
    "fix-failing-test",
    "interval-merge",
    "json-csv",
    "log-parser",
    "regex-validate",
    "sqlite-query",
    "text-template",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """Raised when a scenario package violates its frozen contract."""


class EvidenceState(str, enum.Enum):
    PENDING_DOCKER = "pending-docker"
    VALIDATED = "validated"
    AUDIT_FAILED = "audit-failed"


@dataclasses.dataclass(frozen=True)
class Criterion:
    id: str
    script: pathlib.Path
    script_sha256: str
    kind: str
    expected_status: str
    expected_output: str
    negative_ids: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class NegativeImplementation:
    id: str
    overlay: pathlib.Path
    overlay_sha256: str
    fault: str


@dataclasses.dataclass(frozen=True)
class Evidence:
    path: pathlib.Path
    state: EvidenceState
    payload: Mapping[str, Any]


@dataclasses.dataclass(frozen=True)
class HiddenTest:
    root: pathlib.Path
    test_id: str
    candidate_sha256: str
    dockerfile_sha256: str
    content_identity_sha256: str
    contract_identity_sha256: str
    duplicate_justification: str | None
    entrypoint: str
    criteria: tuple[Criterion, ...]
    reference_overlay: pathlib.Path
    reference_sha256: str
    negative_implementations: tuple[NegativeImplementation, ...]
    evidence: Evidence


@dataclasses.dataclass(frozen=True)
class Scenario:
    root: pathlib.Path
    scenario_id: str
    purpose_sha256: str
    base_skill_sha256: str
    public_paths: tuple[pathlib.Path, ...]
    hidden_tests_dir: pathlib.Path
    expected_test_ids: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class ValidationReport:
    scenario_count: int
    test_count: int
    check_count: int
    scenario_ids: tuple[str, ...]
    pending_evidence: tuple[str, ...]
    audit_failed_evidence: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def runtime_ready(self) -> bool:
        return not self.errors and not self.pending_evidence and not self.audit_failed_evidence


@dataclasses.dataclass(frozen=True)
class WeakScript:
    path: pathlib.Path
    reasons: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class StaticAuditReport:
    total_scripts: int
    weak_scripts: tuple[WeakScript, ...]


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(root: str | pathlib.Path) -> str:
    """Hash every relative path and file byte in an overlay deterministically."""
    root = pathlib.Path(root).resolve()
    if not root.is_dir():
        raise ContractError(f"overlay is not a directory: {root}")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ContractError(f"overlay symlink is forbidden: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def contract_identity_for_manifest(value: Mapping[str, Any]) -> str:
    """Hash every contract field except the self-referential identity field."""
    return canonical_json_hash(
        {key: item for key, item in value.items() if key != "contract_identity_sha256"}
    )


def _read_json(path: pathlib.Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ContractError(f"missing JSON artifact: {path}") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"malformed JSON artifact {path}: {error}") from error
    if not isinstance(value, dict):
        raise ContractError(f"JSON artifact must contain an object: {path}")
    return value


def _require_keys(value: Mapping[str, Any], expected: set[str], path: pathlib.Path) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ContractError(f"wrong fields in {path}: missing={missing}, extra={extra}")


def _safe_path(root: pathlib.Path, value: Any, field: str, *, must_exist: bool = True) -> pathlib.Path:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{field} must be a non-empty relative path")
    relative = pathlib.PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise ContractError(f"{field} must be a safe relative path: {value!r}")
    root = root.resolve()
    path = (root / pathlib.Path(*relative.parts)).resolve()
    if path != root and root not in path.parents:
        raise ContractError(f"{field} relative path escapes package: {value!r}")
    if must_exist and not path.exists():
        raise ContractError(f"missing {field}: {path}")
    if path.is_symlink():
        raise ContractError(f"symlinks are forbidden for {field}: {path}")
    return path


def _require_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ContractError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _bash_syntax(script: pathlib.Path) -> None:
    result = subprocess.run(
        ["bash", "-n", str(script)], text=True, capture_output=True, check=False
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise ContractError(f"bash syntax error in {script}: {detail}")


def _content_identity(
    candidate_hash: str,
    docker_hash: str,
    criteria: Sequence[Criterion],
    reference_hash: str,
    negatives: Sequence[NegativeImplementation],
) -> str:
    digest = hashlib.sha256()
    digest.update(bytes.fromhex(candidate_hash))
    digest.update(bytes.fromhex(docker_hash))
    for criterion in sorted(criteria, key=lambda item: item.id):
        digest.update(bytes.fromhex(criterion.script_sha256))
    digest.update(bytes.fromhex(reference_hash))
    for negative in sorted(negatives, key=lambda item: item.id):
        digest.update(negative.id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(negative.overlay_sha256))
    return digest.hexdigest()


def _load_evidence(
    path: pathlib.Path,
    test_id: str,
    contract_identity_sha256: str,
    criterion_ids: Sequence[str] | None = None,
    *,
    require_fresh_isolation: bool = True,
) -> Evidence:
    value = _read_json(path)
    required = {
        "schema",
        "test_id",
        "state",
        "contract_identity_sha256",
        "reason",
        "reference",
        "negative_implementations",
    }
    # Validated records add immutable execution metadata; pending records do not.
    if not required.issubset(value):
        raise ContractError(f"evidence record missing fields: {path}")
    if value["schema"] != SCHEMA_EVIDENCE or value["test_id"] != test_id:
        raise ContractError(f"evidence identity mismatch: {path}")
    try:
        state = EvidenceState(value["state"])
    except (ValueError, TypeError) as error:
        raise ContractError(f"invalid evidence state in {path}") from error
    if state is EvidenceState.PENDING_DOCKER:
        if not isinstance(value["reason"], str) or not value["reason"].strip():
            raise ContractError(f"pending evidence requires a reason: {path}")
        if value["reference"] is not None or value["negative_implementations"] is not None:
            raise ContractError(f"pending evidence may not invent runtime results: {path}")
        if value["contract_identity_sha256"] is not None:
            raise ContractError(f"pending evidence may not claim a validated contract identity: {path}")
    else:
        required_validated = {
            "validated_at",
            "image_digest",
            "docker_version",
            "reference",
            "starting",
            "negative_implementations",
        }
        if not required_validated.issubset(value):
            raise ContractError(f"validated evidence missing run details: {path}")
        if value["contract_identity_sha256"] != contract_identity_sha256:
            raise ContractError(
                f"stale evidence contract identity in {path}: "
                f"expected {contract_identity_sha256}"
            )
        if state is EvidenceState.VALIDATED:
            if value.get("reference", {}).get("passed") is not True:
                raise ContractError(f"validated evidence does not prove reference success: {path}")
            if value.get("starting", {}).get("rejected") is not True:
                raise ContractError(f"validated evidence does not prove starting-state rejection: {path}")
            negatives = value.get("negative_implementations")
            if not negatives or any(
                result.get("killed_assigned") is not True for result in negatives.values()
            ):
                raise ContractError(f"validated evidence does not prove negative rejection: {path}")
        expected_criteria = set(criterion_ids or ())
        evidence_sets = [
            ("reference", value.get("reference", {}).get("criteria")),
            ("starting", value.get("starting", {}).get("criteria")),
        ]
        evidence_sets.extend(
            (f"negative {negative_id}", result.get("criteria"))
            for negative_id, result in value.get("negative_implementations", {}).items()
        )
        for label, details in evidence_sets:
            if not isinstance(details, dict) or (
                expected_criteria and set(details) != expected_criteria
            ):
                raise ContractError(
                    f"{label} evidence criterion set mismatch in {path}"
                )
            if require_fresh_isolation and any(
                not isinstance(row, dict)
                or row.get("isolation") != "fresh-container-per-criterion"
                for row in details.values()
            ):
                raise ContractError(
                    f"{label} evidence must use fresh-container-per-criterion: {path}"
                )
    return Evidence(path=path, state=state, payload=value)


def load_test(
    test_dir: str | pathlib.Path, *, require_fresh_evidence: bool = True
) -> HiddenTest:
    root = pathlib.Path(test_dir).resolve()
    manifest_path = root / "test.json"
    value = _read_json(manifest_path)
    _require_keys(
        value,
        {
            "schema",
            "test_id",
            "candidate_sha256",
            "dockerfile_sha256",
            "content_identity_sha256",
            "contract_identity_sha256",
            "duplicate_justification",
            "entrypoint",
            "criteria",
            "reference_overlay",
            "reference_sha256",
            "negative_implementations",
            "validation_evidence",
        },
        manifest_path,
    )
    if value["schema"] != SCHEMA_TEST:
        raise ContractError(f"unsupported hidden-test schema in {manifest_path}")
    expected_id = f"{root.parents[1].name}/{root.name}"
    if value["test_id"] != expected_id:
        raise ContractError(f"unstable test_id in {manifest_path}: expected {expected_id}")
    candidate = root / "candidate.json"
    dockerfile = root / "Dockerfile"
    if not candidate.is_file() or not dockerfile.is_file():
        raise ContractError(f"hidden test lacks candidate.json or Dockerfile: {root}")
    _read_json(candidate)
    candidate_hash = _require_digest(value["candidate_sha256"], "candidate_sha256")
    docker_hash = _require_digest(value["dockerfile_sha256"], "dockerfile_sha256")
    if candidate_hash != _sha256(candidate):
        raise ContractError(f"candidate_sha256 mismatch: {manifest_path}")
    if docker_hash != _sha256(dockerfile):
        raise ContractError(f"dockerfile_sha256 mismatch: {manifest_path}")
    if not isinstance(value["entrypoint"], str) or not value["entrypoint"].strip():
        raise ContractError(f"entrypoint must be non-empty: {manifest_path}")
    raw_criteria = value["criteria"]
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise ContractError(f"criteria must be a non-empty list: {manifest_path}")
    criteria: list[Criterion] = []
    criterion_ids: set[str] = set()
    for raw in raw_criteria:
        if not isinstance(raw, dict):
            raise ContractError(f"criterion must be an object: {manifest_path}")
        _require_keys(
            raw,
            {"id", "script", "script_sha256", "kind", "expected", "negative_ids"},
            manifest_path,
        )
        criterion_id = raw["id"]
        if not isinstance(criterion_id, str) or not criterion_id or criterion_id in criterion_ids:
            raise ContractError(f"duplicate or invalid criterion id: {criterion_id!r}")
        criterion_ids.add(criterion_id)
        script = _safe_path(root, raw["script"], "criterion script")
        if not script.is_file() or script.suffix != ".sh":
            raise ContractError(f"criterion script must be a .sh file: {script}")
        _bash_syntax(script)
        script_hash = _require_digest(raw["script_sha256"], "script_sha256")
        if script_hash != _sha256(script):
            raise ContractError(f"script_sha256 mismatch: {script}")
        kind = raw["kind"]
        if kind not in {"functional", "error", "integrity", "performance"}:
            raise ContractError(f"invalid criterion kind {kind!r}: {manifest_path}")
        expected = raw["expected"]
        if not isinstance(expected, dict) or set(expected) != {"status", "output"}:
            raise ContractError(f"criterion expected semantics malformed: {manifest_path}")
        if expected["status"] not in {"zero", "nonzero"}:
            raise ContractError(f"criterion expected status malformed: {manifest_path}")
        if not isinstance(expected["output"], str) or not expected["output"].strip():
            raise ContractError(f"criterion expected output missing: {manifest_path}")
        negative_ids = raw["negative_ids"]
        if not isinstance(negative_ids, list) or not negative_ids or not all(
            isinstance(item, str) and item for item in negative_ids
        ):
            raise ContractError(f"criterion must name negative implementations: {manifest_path}")
        criteria.append(
            Criterion(
                id=criterion_id,
                script=script,
                script_sha256=script_hash,
                kind=kind,
                expected_status=expected["status"],
                expected_output=expected["output"],
                negative_ids=tuple(negative_ids),
            )
        )
    actual_scripts = {path.resolve() for path in (root / "checks").glob("*.sh")}
    declared_scripts = {criterion.script for criterion in criteria}
    if actual_scripts != declared_scripts:
        raise ContractError(
            f"criterion scripts differ from checks directory: missing={actual_scripts-declared_scripts}, "
            f"extra={declared_scripts-actual_scripts}"
        )
    if not any(criterion.kind == "functional" for criterion in criteria):
        raise ContractError(f"at least one functional criterion is required: {manifest_path}")
    raw_negatives = value["negative_implementations"]
    if not isinstance(raw_negatives, list) or not raw_negatives:
        raise ContractError(f"at least one negative implementation is required: {manifest_path}")
    negatives: list[NegativeImplementation] = []
    negative_ids: set[str] = set()
    for raw in raw_negatives:
        if not isinstance(raw, dict) or set(raw) != {
            "id",
            "overlay",
            "overlay_sha256",
            "fault",
        }:
            raise ContractError(f"negative implementation malformed: {manifest_path}")
        negative_id = raw["id"]
        if not isinstance(negative_id, str) or not negative_id or negative_id in negative_ids:
            raise ContractError(f"duplicate or invalid negative id: {negative_id!r}")
        negative_ids.add(negative_id)
        overlay = _safe_path(root, raw["overlay"], "negative overlay")
        if not overlay.is_dir() or not any(path.is_file() for path in overlay.rglob("*")):
            raise ContractError(f"negative overlay must contain files: {overlay}")
        overlay_hash = _require_digest(raw["overlay_sha256"], "overlay_sha256")
        if overlay_hash != tree_hash(overlay):
            raise ContractError(f"overlay_sha256 mismatch: {overlay}")
        if not isinstance(raw["fault"], str) or not raw["fault"].strip():
            raise ContractError(f"negative implementation requires a fault description: {manifest_path}")
        negatives.append(
            NegativeImplementation(negative_id, overlay, overlay_hash, raw["fault"])
        )
    for criterion in criteria:
        unknown = set(criterion.negative_ids) - negative_ids
        if unknown:
            raise ContractError(f"criterion {criterion.id} names unknown negatives: {sorted(unknown)}")
    reference = _safe_path(root, value["reference_overlay"], "reference overlay")
    if not reference.is_dir() or not any(path.is_file() for path in reference.rglob("*")):
        raise ContractError(f"reference overlay must contain files: {reference}")
    reference_hash = _require_digest(value["reference_sha256"], "reference_sha256")
    if reference_hash != tree_hash(reference):
        raise ContractError(f"reference_sha256 mismatch: {reference}")
    identity = _require_digest(value["content_identity_sha256"], "content_identity_sha256")
    calculated_identity = _content_identity(
        candidate_hash, docker_hash, criteria, reference_hash, negatives
    )
    if identity != calculated_identity:
        raise ContractError(f"content_identity_sha256 mismatch: {manifest_path}")
    contract_identity = _require_digest(
        value["contract_identity_sha256"], "contract_identity_sha256"
    )
    calculated_contract_identity = contract_identity_for_manifest(value)
    if contract_identity != calculated_contract_identity:
        raise ContractError(f"contract_identity_sha256 mismatch: {manifest_path}")
    evidence_path = _safe_path(root, value["validation_evidence"], "validation evidence")
    evidence = _load_evidence(
        evidence_path,
        expected_id,
        contract_identity,
        tuple(criterion.id for criterion in criteria),
        require_fresh_isolation=require_fresh_evidence,
    )
    justification = value["duplicate_justification"]
    if justification is not None and (not isinstance(justification, str) or not justification.strip()):
        raise ContractError(f"duplicate_justification must be null or non-empty: {manifest_path}")
    return HiddenTest(
        root=root,
        test_id=expected_id,
        candidate_sha256=candidate_hash,
        dockerfile_sha256=docker_hash,
        content_identity_sha256=identity,
        contract_identity_sha256=contract_identity,
        duplicate_justification=justification,
        entrypoint=value["entrypoint"],
        criteria=tuple(criteria),
        reference_overlay=reference,
        reference_sha256=reference_hash,
        negative_implementations=tuple(negatives),
        evidence=evidence,
    )


def load_scenario(scenario_dir: str | pathlib.Path) -> Scenario:
    root = pathlib.Path(scenario_dir).resolve()
    manifest_path = root / "scenario.json"
    value = _read_json(manifest_path)
    _require_keys(
        value,
        {
            "schema",
            "scenario_id",
            "purpose_sha256",
            "base_skill_sha256",
            "public_paths",
            "hidden_tests",
            "boundary",
        },
        manifest_path,
    )
    if value["schema"] != SCHEMA_SCENARIO or value["scenario_id"] != root.name:
        raise ContractError(f"scenario identity mismatch: {manifest_path}")
    purpose_hash = _require_digest(value["purpose_sha256"], "purpose_sha256")
    base_hash = _require_digest(value["base_skill_sha256"], "base_skill_sha256")
    if purpose_hash != _sha256(root / "scenario.md"):
        raise ContractError(f"purpose_sha256 mismatch: {manifest_path}")
    if base_hash != _sha256(root / "base_skill/SKILL.md"):
        raise ContractError(f"base_skill_sha256 mismatch: {manifest_path}")
    raw_public = value["public_paths"]
    if not isinstance(raw_public, list) or not raw_public:
        raise ContractError(f"public_paths must be non-empty: {manifest_path}")
    public_paths = tuple(_safe_path(root, item, "public path") for item in raw_public)
    hidden = value["hidden_tests"]
    if not isinstance(hidden, dict) or set(hidden) != {"path", "count", "ids"}:
        raise ContractError(f"hidden_tests contract malformed: {manifest_path}")
    hidden_dir = _safe_path(root, hidden["path"], "hidden tests")
    if hidden_dir in public_paths or any(hidden_dir in path.parents for path in public_paths):
        raise ContractError(f"hidden tests overlap public paths: {manifest_path}")
    expected_ids = tuple(f"{root.name}/t{i}" for i in range(1, 11))
    if hidden["count"] != 10 or tuple(hidden["ids"]) != expected_ids:
        raise ContractError(f"hidden test IDs must be exactly t1..t10: {manifest_path}")
    boundary = value["boundary"]
    if not isinstance(boundary, dict) or set(boundary) != {"public_can_read", "forbidden_to_campaign"}:
        raise ContractError(f"boundary contract malformed: {manifest_path}")
    if boundary["forbidden_to_campaign"] != ["tests"]:
        raise ContractError(f"hidden tests must be forbidden to campaigns: {manifest_path}")
    return Scenario(
        root=root,
        scenario_id=root.name,
        purpose_sha256=purpose_hash,
        base_skill_sha256=base_hash,
        public_paths=public_paths,
        hidden_tests_dir=hidden_dir,
        expected_test_ids=expected_ids,
    )


def _files_under(paths: Iterable[pathlib.Path]) -> Iterable[pathlib.Path]:
    for path in paths:
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from (child for child in path.rglob("*") if child.is_file())


def exact_id_error(
    actual: Sequence[str], expected: Sequence[str], label: str
) -> str | None:
    """Describe missing/extra stable IDs, including duplicate directory entries."""
    actual_set = set(actual)
    expected_set = set(expected)
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    duplicate_count = len(actual) - len(actual_set)
    if missing or extra or duplicate_count:
        return (
            f"missing/extra hidden tests for {label}: missing={missing}, extra={extra}, "
            f"duplicate_entries={duplicate_count}"
        )
    return None


def duplicate_identity_errors(
    rows: Iterable[tuple[str, str, str | None]],
) -> tuple[str, ...]:
    """Require both sides of every intentional duplicate to justify the pairing."""
    seen: dict[str, tuple[str, str | None]] = {}
    errors: list[str] = []
    for identity, test_id, justification in rows:
        prior = seen.get(identity)
        if prior is not None:
            prior_id, prior_justification = prior
            if not (prior_justification and justification):
                errors.append(
                    f"duplicate content identity without justification: {prior_id}, {test_id}"
                )
        else:
            seen[identity] = (test_id, justification)
    return tuple(errors)


def public_leakage_matches(
    public_paths: Iterable[pathlib.Path], needles: Iterable[tuple[str, str]]
) -> tuple[str, ...]:
    """Find exact hidden artifacts embedded in campaign-visible UTF-8 files."""
    needle_rows = tuple(needles)
    errors: list[str] = []
    for public_file in _files_under(public_paths):
        try:
            text = public_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, needle in needle_rows:
            if needle in text:
                errors.append(f"public leakage in {public_file}: contains {label}")
    return tuple(errors)


def _leakage_errors(contract: Scenario, tests: Sequence[HiddenTest]) -> list[str]:
    needles: list[tuple[str, str]] = [("hidden path", "tests/t")]
    for hidden in tests:
        candidate = _read_json(hidden.root / "candidate.json")
        prompt = candidate.get("prompt")
        if isinstance(prompt, str) and len(prompt) >= 40:
            needles.append((hidden.test_id, prompt))
        for script in (hidden.root / "checks").glob("*.sh"):
            source = script.read_text(encoding="utf-8")
            if len(source) >= 80:
                needles.append((f"{hidden.test_id}:{script.name}", source))
    return list(public_leakage_matches(contract.public_paths, needles))


def validate_root(root: str | pathlib.Path) -> ValidationReport:
    root = pathlib.Path(root).resolve()
    scenario_dirs = sorted(path for path in root.iterdir() if (path / "scenario.md").is_file())
    scenario_ids = tuple(path.name for path in scenario_dirs)
    errors: list[str] = []
    pending: list[str] = []
    audit_failed: list[str] = []
    tests: list[HiddenTest] = []
    check_count = 0
    if tuple(sorted(scenario_ids)) != tuple(sorted(CANONICAL_SCENARIOS)):
        errors.append(
            f"scenario set mismatch: expected={list(CANONICAL_SCENARIOS)}, actual={list(scenario_ids)}"
        )
    identity_rows: list[tuple[str, str, str | None]] = []
    for scenario_dir in scenario_dirs:
        try:
            scenario = load_scenario(scenario_dir)
            actual_dirs = sorted(
                path for path in scenario.hidden_tests_dir.iterdir() if path.is_dir()
            )
            actual_ids = tuple(f"{scenario.scenario_id}/{path.name}" for path in actual_dirs)
            id_error = exact_id_error(actual_ids, scenario.expected_test_ids, scenario.scenario_id)
            if id_error:
                raise ContractError(id_error)
            scenario_tests: list[HiddenTest] = []
            for test_dir in actual_dirs:
                hidden = load_test(test_dir)
                scenario_tests.append(hidden)
                tests.append(hidden)
                check_count += len(hidden.criteria)
                if hidden.evidence.state is EvidenceState.PENDING_DOCKER:
                    pending.append(hidden.test_id)
                elif hidden.evidence.state is EvidenceState.AUDIT_FAILED:
                    audit_failed.append(hidden.test_id)
                    errors.append(f"runtime oracle audit failed: {hidden.test_id}")
                identity_rows.append(
                    (
                        hidden.content_identity_sha256,
                        hidden.test_id,
                        hidden.duplicate_justification,
                    )
                )
            errors.extend(_leakage_errors(scenario, scenario_tests))
        except (ContractError, OSError, TypeError) as error:
            errors.append(str(error))
    errors.extend(duplicate_identity_errors(identity_rows))
    return ValidationReport(
        scenario_count=len(scenario_dirs),
        test_count=len(tests),
        check_count=check_count,
        scenario_ids=scenario_ids,
        pending_evidence=tuple(sorted(pending)),
        audit_failed_evidence=tuple(sorted(audit_failed)),
        errors=tuple(errors),
    )


def _script_weakness(script: pathlib.Path) -> tuple[str, ...]:
    """Return strict static weaknesses; Docker mutation remains the authority."""
    source = script.read_text(encoding="utf-8")
    relative = script.as_posix()
    reasons: list[str] = []
    scenario = script.parents[3].name
    test_id = script.parents[1].name
    name = script.name
    # Pure Python assertion checks propagate import/assertion failure directly.  The
    # other classes historically masked status through pipelines/substitutions or
    # accepted stale output and therefore use the audited v1 execution pattern.
    needs_v1 = scenario in {
        "config-parser",
        "csv-stats",
        "json-csv",
        "log-parser",
        "sqlite-query",
        "text-template",
    }
    if scenario == "argparse-cli" and name not in {"help-lists.sh", "help-ok.sh", "exit-zero.sh"}:
        needs_v1 = True
    if scenario == "fix-failing-test" and name == "tests-unedited.sh":
        needs_v1 = True
    if scenario == "interval-merge" and test_id == "t6":
        needs_v1 = True
    if needs_v1:
        if "# skillrace-oracle-v1" not in source:
            reasons.append("missing audited execution pattern")
        if scenario != "fix-failing-test" and "[ -f " not in source:
            reasons.append("implementation artifact not required")
        if scenario not in {"fix-failing-test", "interval-merge"} and "rc=$?" not in source:
            reasons.append("entrypoint status not captured")
    if scenario in {"json-csv", "text-template"} and "rm -f " not in source:
        reasons.append("stale output not deleted")
    if scenario == "csv-stats" and test_id == "t9" and name == "fast.sh":
        for token in ("timeout ", "rc=$?", "5050"):
            if token not in source:
                reasons.append(f"performance oracle lacks {token.strip()}")
    if scenario == "interval-merge" and test_id == "t6" and "result" not in source:
        reasons.append("no returned-result assertion")
    if scenario == "fix-failing-test" and name == "tests-unedited.sh":
        if not (
            "/check/oracle/assets/integrity_check.py" in source
            or "# content-addressed-integrity-v1" in source
        ):
            reasons.append("content-addressed integrity checker not present")
    return tuple(reasons)


def audit_static(root: str | pathlib.Path) -> StaticAuditReport:
    scripts = sorted(pathlib.Path(root).resolve().glob("*/tests/t*/checks/*.sh"))
    weak = tuple(
        WeakScript(path=script, reasons=reasons)
        for script in scripts
        if (reasons := _script_weakness(script))
    )
    return StaticAuditReport(total_scripts=len(scripts), weak_scripts=weak)


def _test_static_report(contract: HiddenTest) -> StaticAuditReport:
    weak = tuple(
        WeakScript(path=criterion.script, reasons=reasons)
        for criterion in contract.criteria
        if (reasons := _script_weakness(criterion.script))
    )
    return StaticAuditReport(total_scripts=len(contract.criteria), weak_scripts=weak)


def _refresh_test_document(test_dir: pathlib.Path) -> tuple[pathlib.Path, Mapping[str, Any]]:
    """Calculate refreshed test digests without writing any files."""
    test_dir = test_dir.resolve()
    manifest_path = test_dir / "test.json"
    original = dict(_read_json(manifest_path))
    value = dict(original)
    if value.get("schema") != SCHEMA_TEST:
        raise ContractError(f"unsupported hidden-test schema in {manifest_path}")
    candidate = test_dir / "candidate.json"
    dockerfile = test_dir / "Dockerfile"
    if not candidate.is_file() or not dockerfile.is_file():
        raise ContractError(f"hidden test lacks candidate.json or Dockerfile: {test_dir}")
    _read_json(candidate)
    criteria = value.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ContractError(f"criteria must be a non-empty list: {manifest_path}")
    refreshed_criteria: list[Mapping[str, Any]] = []
    identity_rows: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for raw in criteria:
        if not isinstance(raw, dict):
            raise ContractError(f"criterion must be an object: {manifest_path}")
        criterion_id = raw.get("id")
        if not isinstance(criterion_id, str) or not criterion_id or criterion_id in seen_ids:
            raise ContractError(f"duplicate or invalid criterion id: {criterion_id!r}")
        seen_ids.add(criterion_id)
        script = _safe_path(test_dir, raw.get("script"), "criterion script")
        if not script.is_file() or script.suffix != ".sh":
            raise ContractError(f"criterion script must be a .sh file: {script}")
        _bash_syntax(script)
        script_hash = _sha256(script)
        refreshed = dict(raw)
        refreshed["script_sha256"] = script_hash
        refreshed_criteria.append(refreshed)
        identity_rows.append((criterion_id, script_hash))
    candidate_hash = _sha256(candidate)
    docker_hash = _sha256(dockerfile)
    reference = _safe_path(test_dir, value.get("reference_overlay"), "reference overlay")
    if not reference.is_dir():
        raise ContractError(f"reference overlay must be a directory: {reference}")
    reference_hash = tree_hash(reference)
    raw_negatives = value.get("negative_implementations")
    if not isinstance(raw_negatives, list) or not raw_negatives:
        raise ContractError(f"negative implementations must be non-empty: {manifest_path}")
    refreshed_negatives: list[Mapping[str, Any]] = []
    negative_rows: list[tuple[str, str]] = []
    seen_negative_ids: set[str] = set()
    for raw in raw_negatives:
        if not isinstance(raw, dict):
            raise ContractError(f"negative implementation must be an object: {manifest_path}")
        negative_id = raw.get("id")
        if (
            not isinstance(negative_id, str)
            or not negative_id
            or negative_id in seen_negative_ids
        ):
            raise ContractError(f"duplicate or invalid negative id: {negative_id!r}")
        seen_negative_ids.add(negative_id)
        overlay = _safe_path(test_dir, raw.get("overlay"), "negative overlay")
        if not overlay.is_dir():
            raise ContractError(f"negative overlay must be a directory: {overlay}")
        overlay_hash = tree_hash(overlay)
        refreshed = dict(raw)
        refreshed["overlay_sha256"] = overlay_hash
        refreshed_negatives.append(refreshed)
        negative_rows.append((negative_id, overlay_hash))
    identity = hashlib.sha256(bytes.fromhex(candidate_hash) + bytes.fromhex(docker_hash))
    for _, script_hash in sorted(identity_rows):
        identity.update(bytes.fromhex(script_hash))
    identity.update(bytes.fromhex(reference_hash))
    for negative_id, overlay_hash in sorted(negative_rows):
        identity.update(negative_id.encode("utf-8"))
        identity.update(b"\0")
        identity.update(bytes.fromhex(overlay_hash))
    value["candidate_sha256"] = candidate_hash
    value["dockerfile_sha256"] = docker_hash
    value["content_identity_sha256"] = identity.hexdigest()
    value["criteria"] = refreshed_criteria
    value["reference_sha256"] = reference_hash
    value["negative_implementations"] = refreshed_negatives
    value["contract_identity_sha256"] = contract_identity_for_manifest(value)
    evidence_path = _safe_path(
        test_dir, value.get("validation_evidence"), "validation evidence"
    )
    evidence = _read_json(evidence_path)
    if evidence.get("state") in {
        EvidenceState.VALIDATED.value,
        EvidenceState.AUDIT_FAILED.value,
    }:
        old_identity = original.get("contract_identity_sha256")
        new_identity = value["contract_identity_sha256"]
        if evidence.get("contract_identity_sha256") != old_identity or new_identity != old_identity:
            raise ContractError(
                f"stale validated evidence prevents hash refresh for {manifest_path}; "
                "reset evidence explicitly to pending-docker first"
            )
    return manifest_path, value


def _refresh_scenario_document(
    scenario_dir: pathlib.Path,
) -> tuple[pathlib.Path, Mapping[str, Any]]:
    """Calculate refreshed public-source digests without writing any files."""
    scenario_dir = scenario_dir.resolve()
    manifest_path = scenario_dir / "scenario.json"
    value = dict(_read_json(manifest_path))
    if value.get("schema") != SCHEMA_SCENARIO:
        raise ContractError(f"unsupported scenario schema in {manifest_path}")
    purpose = scenario_dir / "scenario.md"
    base_skill = scenario_dir / "base_skill/SKILL.md"
    if not purpose.is_file() or not base_skill.is_file():
        raise ContractError(f"scenario lacks purpose or base skill: {scenario_dir}")
    value["purpose_sha256"] = _sha256(purpose)
    value["base_skill_sha256"] = _sha256(base_skill)
    return manifest_path, value


def refresh_hashes(root: str | pathlib.Path) -> tuple[pathlib.Path, ...]:
    """Atomically refresh only contract-owned digest fields under ``root``.

    All documents are calculated and validated before the first replacement. Evidence
    files are neither opened for writing nor included in the returned paths.
    """
    root = pathlib.Path(root).resolve()
    documents: list[tuple[pathlib.Path, Mapping[str, Any]]] = []
    if (root / "test.json").is_file():
        documents.append(_refresh_test_document(root))
    elif (root / "scenario.json").is_file():
        documents.append(_refresh_scenario_document(root))
        documents.extend(
            _refresh_test_document(test_dir)
            for test_dir in sorted((root / "tests").glob("t*"))
            if test_dir.is_dir()
        )
    else:
        scenario_dirs = sorted(path for path in root.iterdir() if (path / "scenario.json").is_file())
        if not scenario_dirs:
            raise ContractError(f"no scenario or hidden-test contracts under {root}")
        for scenario_dir in scenario_dirs:
            documents.append(_refresh_scenario_document(scenario_dir))
            documents.extend(
                _refresh_test_document(test_dir)
                for test_dir in sorted((scenario_dir / "tests").glob("t*"))
                if test_dir.is_dir()
            )
    changed: list[pathlib.Path] = []
    for path, value in documents:
        if _read_json(path) != value:
            atomic_write_json(path, value)
            changed.append(path)
    return tuple(changed)


def _print_report(report: ValidationReport, static: StaticAuditReport) -> None:
    print(
        json.dumps(
            {
                "scenarios": report.scenario_count,
                "tests": report.test_count,
                "checks": report.check_count,
                "pending_docker": len(report.pending_evidence),
                "audit_failed": len(report.audit_failed_evidence),
                "runtime_ready": report.runtime_ready,
                "errors": list(report.errors),
                "weak_scripts": [
                    {"path": str(item.path), "reasons": list(item.reasons)}
                    for item in static.weak_scripts
                ],
            },
            indent=2,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    target = validate.add_mutually_exclusive_group(required=True)
    target.add_argument("root", nargs="?", type=pathlib.Path)
    target.add_argument("--test", type=pathlib.Path)
    validate.add_argument("--require-runtime-evidence", action="store_true")
    refresh = subparsers.add_parser("refresh-hashes")
    refresh.add_argument("root", type=pathlib.Path)
    arguments = parser.parse_args(argv)
    if arguments.command == "refresh-hashes":
        for changed in refresh_hashes(arguments.root):
            print(f"refreshed {changed}")
        return 0
    if arguments.test is not None:
        try:
            contract = load_test(arguments.test)
            static = _test_static_report(contract)
            payload = {
                "test_id": contract.test_id,
                "checks": len(contract.criteria),
                "pending_docker": contract.evidence.state is EvidenceState.PENDING_DOCKER,
                "audit_failed": contract.evidence.state is EvidenceState.AUDIT_FAILED,
                "runtime_ready": contract.evidence.state is EvidenceState.VALIDATED,
                "errors": (
                    [f"runtime oracle audit failed: {contract.test_id}"]
                    if contract.evidence.state is EvidenceState.AUDIT_FAILED
                    else []
                ),
                "weak_scripts": [
                    {"path": str(item.path), "reasons": list(item.reasons)}
                    for item in static.weak_scripts
                ],
            }
            print(json.dumps(payload, indent=2))
            failed = bool(
                static.weak_scripts
                or contract.evidence.state is EvidenceState.AUDIT_FAILED
            )
            if arguments.require_runtime_evidence:
                failed = failed or contract.evidence.state is not EvidenceState.VALIDATED
            return int(failed)
        except (ContractError, OSError, TypeError) as error:
            print(json.dumps({"test_id": str(arguments.test), "errors": [str(error)]}, indent=2))
            return 1
    report = validate_root(arguments.root)
    static = audit_static(arguments.root)
    _print_report(report, static)
    failed = bool(report.errors or report.audit_failed_evidence or static.weak_scripts)
    if arguments.require_runtime_evidence:
        failed = failed or bool(report.pending_evidence)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
