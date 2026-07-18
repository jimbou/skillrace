from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, ClassVar, Mapping

from .runtime.providers import resolve_model


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _to_dict(record: Any, schema: str) -> dict[str, Any]:
    return {"schema": schema, **_json_value(asdict(record))}


def _record_values(
    record_type: type[Any], data: Mapping[str, Any], schema: str
) -> dict[str, Any]:
    if data.get("schema") != schema:
        raise ValueError(f"schema must be {schema}")
    expected = {field.name for field in fields(record_type)}
    supplied = set(data) - {"schema"}
    unknown = supplied - expected
    missing = expected - supplied
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing fields: {sorted(missing)}")
    return {name: data[name] for name in expected}


@dataclass(frozen=True)
class ExperimentConfig:
    SCHEMA: ClassVar[str] = "skillrace-experiment-config/1"

    experiment_id: str
    part: str
    methods: tuple[str, ...]
    replicate_count: int
    provider: str
    model_id: str
    pi_version: str
    role_budgets: dict[str, int]
    verifier_backend: str
    verifier_command: tuple[str, ...]
    verifier_model: str
    verifier_reasoning: str
    docker_image: str
    resource_limits: dict[str, str | int]
    network_policy: str
    timeouts: dict[str, int]
    suite_path: Path
    scenario_path: Path
    iteration_budget: int
    live: bool
    output_root: Path
    heldout_repetitions: int

    def __post_init__(self) -> None:
        if self.part not in {"part1", "part2"}:
            raise ValueError("part must be part1 or part2")
        resolve_model(self.provider, self.model_id)
        if self.verifier_backend != "codex":
            raise ValueError("verifier backend must be codex, not Pi")
        required_timeouts = {"provider", "pi", "docker", "codex", "check", "patch"}
        if set(self.timeouts) != required_timeouts:
            raise ValueError(f"timeouts must contain {sorted(required_timeouts)}")
        if any(value <= 0 for value in self.timeouts.values()):
            raise ValueError("timeouts must be positive")
        if self.replicate_count <= 0:
            raise ValueError("replicate_count must be positive")
        if self.iteration_budget <= 0:
            raise ValueError("iteration_budget must be positive")
        if self.heldout_repetitions <= 0:
            raise ValueError("heldout_repetitions must be positive")

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self, self.SCHEMA)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExperimentConfig":
        values = _record_values(cls, data, cls.SCHEMA)
        values["methods"] = tuple(values["methods"])
        values["verifier_command"] = tuple(values["verifier_command"])
        values["role_budgets"] = dict(values["role_budgets"])
        values["resource_limits"] = dict(values["resource_limits"])
        values["timeouts"] = dict(values["timeouts"])
        values["suite_path"] = Path(values["suite_path"])
        values["scenario_path"] = Path(values["scenario_path"])
        values["output_root"] = Path(values["output_root"])
        return cls(**values)


@dataclass(frozen=True)
class SkillVersion:
    SCHEMA: ClassVar[str] = "skillrace-skill-version/1"

    skill_id: str
    version_id: str
    parent_version_id: str | None
    directory_path: Path
    tree_hash: str
    creation_role: str
    model_id: str
    receipt_path: Path

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self, self.SCHEMA)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SkillVersion":
        values = _record_values(cls, data, cls.SCHEMA)
        values["directory_path"] = Path(values["directory_path"])
        values["receipt_path"] = Path(values["receipt_path"])
        return cls(**values)


@dataclass(frozen=True)
class TestCase:
    SCHEMA: ClassVar[str] = "skillrace-test-case/1"

    test_id: str
    prompt_path: Path
    prompt_hash: str
    environment_directory: Path
    environment_hash: str
    nl_check_path: Path
    nl_check_hash: str
    origin_method: str
    proposal_receipt: Path
    validation_status: str
    validation_diagnostic: str
    container_image_id: str

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self, self.SCHEMA)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TestCase":
        values = _record_values(cls, data, cls.SCHEMA)
        for name in (
            "prompt_path",
            "environment_directory",
            "nl_check_path",
            "proposal_receipt",
        ):
            values[name] = Path(values[name])
        return cls(**values)


@dataclass(frozen=True)
class RunRecord:
    SCHEMA: ClassVar[str] = "skillrace-run-record/1"
    TERMINATION_STATUSES: ClassVar[set[str]] = {
        "completed",
        "agent_timeout",
        "container_error",
        "provider_error",
    }

    run_id: str
    test_id: str
    skill_id: str
    skill_version_id: str
    method: str
    model_id: str
    budget: int
    container_id: str
    image_id: str
    started_at: str
    ended_at: str
    termination_status: str
    artifact_path: Path
    artifact_hash: str
    trace_path: Path
    tool_log_path: Path
    stdout_path: Path
    stderr_path: Path
    provider_receipt_paths: tuple[Path, ...]
    cost_totals: dict[str, float | int | str]

    def __post_init__(self) -> None:
        if self.termination_status not in self.TERMINATION_STATUSES:
            raise ValueError(f"unknown termination_status: {self.termination_status}")

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self, self.SCHEMA)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunRecord":
        values = _record_values(cls, data, cls.SCHEMA)
        for name in (
            "artifact_path",
            "trace_path",
            "tool_log_path",
            "stdout_path",
            "stderr_path",
        ):
            values[name] = Path(values[name])
        values["provider_receipt_paths"] = tuple(
            Path(path) for path in values["provider_receipt_paths"]
        )
        values["cost_totals"] = dict(values["cost_totals"])
        return cls(**values)


@dataclass(frozen=True)
class CheckBundle:
    SCHEMA: ClassVar[str] = "skillrace-check-bundle/1"

    bundle_id: str
    run_id: str
    artifact_hash: str
    input_hashes: dict[str, str]
    manifest_path: Path
    script_paths: tuple[Path, ...]
    codex_receipt_path: Path

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self, self.SCHEMA)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckBundle":
        values = _record_values(cls, data, cls.SCHEMA)
        values["input_hashes"] = dict(values["input_hashes"])
        values["manifest_path"] = Path(values["manifest_path"])
        values["script_paths"] = tuple(Path(path) for path in values["script_paths"])
        values["codex_receipt_path"] = Path(values["codex_receipt_path"])
        return cls(**values)


@dataclass(frozen=True)
class CheckResults:
    SCHEMA: ClassVar[str] = "skillrace-check-results/1"

    results_id: str
    run_id: str
    check_bundle_hash: str
    artifact_hash_before: str
    artifact_hash_after: str
    artifact_unchanged: bool
    results: tuple[dict[str, Any], ...]
    results_path: Path

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self, self.SCHEMA)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckResults":
        values = _record_values(cls, data, cls.SCHEMA)
        values["results"] = tuple(dict(item) for item in values["results"])
        values["results_path"] = Path(values["results_path"])
        return cls(**values)


@dataclass(frozen=True)
class PatchAttempt:
    SCHEMA: ClassVar[str] = "skillrace-patch-attempt/1"

    patch_attempt_id: str
    input_skill_hash: str
    evidence_bundle_hash: str
    method: str
    model_id: str
    pi_trace_path: Path
    cost_receipt_path: Path
    candidate_skill_hash: str
    patch_status: str
    replay_path: Path | None
    acceptance_status: str

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self, self.SCHEMA)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PatchAttempt":
        values = _record_values(cls, data, cls.SCHEMA)
        values["pi_trace_path"] = Path(values["pi_trace_path"])
        values["cost_receipt_path"] = Path(values["cost_receipt_path"])
        if values["replay_path"] is not None:
            values["replay_path"] = Path(values["replay_path"])
        return cls(**values)


@dataclass(frozen=True)
class ImprovementStep:
    SCHEMA: ClassVar[str] = "skillrace-improvement-step/1"

    iteration: int
    input_skill_version_id: str
    test_id: str
    run_id: str
    check_results_id: str
    patch_attempt_id: str | None
    decision: str
    resulting_skill_version_id: str
    regression_results: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self, self.SCHEMA)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ImprovementStep":
        values = _record_values(cls, data, cls.SCHEMA)
        values["regression_results"] = tuple(
            dict(item) for item in values["regression_results"]
        )
        return cls(**values)
