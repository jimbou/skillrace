import json
from pathlib import Path

import pytest

from skillrace_next.config import freeze_config, load_config
from skillrace_next.records import ExperimentConfig
from skillrace_next.storage import canonical_json_hash


def valid_config_dict() -> dict[str, object]:
    return {
        "schema": "skillrace-experiment-config/1",
        "experiment_id": "development",
        "part": "part1",
        "methods": ["random", "verigrey", "skillrace"],
        "replicate_count": 1,
        "provider": "yunwu",
        "model_id": "deepseek-v3.2",
        "pi_version": "0.73.1",
        "role_budgets": {"proposer": 4, "weak_agent": 4, "patcher": 6},
        "verifier_backend": "codex",
        "verifier_command": ["codex", "exec"],
        "verifier_model": "gpt-5.6-terra",
        "verifier_reasoning": "medium",
        "docker_image": "skillrace-next-development:latest",
        "resource_limits": {"cpus": "1", "memory_mb": 512},
        "network_policy": "none",
        "timeouts": {
            "provider": 60,
            "pi": 180,
            "docker": 180,
            "codex": 300,
            "check": 60,
            "patch": 300,
        },
        "suite_path": "tests_next/fixtures/suite",
        "scenario_path": "tests_next/fixtures/scenario",
        "iteration_budget": 2,
        "live": False,
        "output_root": "out/development",
        "heldout_repetitions": 1,
    }


def write_config(tmp_path: Path, values: dict[str, object]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def test_load_config_returns_validated_frozen_record(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, valid_config_dict()))

    assert isinstance(config, ExperimentConfig)
    assert config.provider == "yunwu"
    assert config.output_root == Path("out/development")
    with pytest.raises(AttributeError):
        config.model_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"unexpected": True}, "unknown"),
        ({"provider": "other"}, "provider"),
        ({"verifier_backend": "pi"}, "verifier"),
    ],
)
def test_load_config_rejects_invalid_values(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    values = valid_config_dict()
    values.update(change)

    with pytest.raises(ValueError, match=message):
        load_config(write_config(tmp_path, values))


def test_load_config_rejects_missing_required_timeout(tmp_path: Path) -> None:
    values = valid_config_dict()
    timeouts = dict(values["timeouts"])  # type: ignore[arg-type]
    del timeouts["patch"]
    values["timeouts"] = timeouts

    with pytest.raises(ValueError, match="timeouts"):
        load_config(write_config(tmp_path, values))


def test_freeze_config_writes_normalized_config_and_hash(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, valid_config_dict()))
    output = tmp_path / "run"

    digest = freeze_config(config, output)

    frozen = json.loads((output / "config.json").read_text(encoding="utf-8"))
    assert frozen == config.to_dict()
    assert digest == canonical_json_hash(frozen)
    assert (output / "config.sha256").read_text(encoding="utf-8") == f"{digest}\n"


def test_development_fixture_freezes_required_model_roles() -> None:
    config = load_config(Path("tests_next/fixtures/development.deepseek-v3.2.json"))

    assert config.provider == "yunwu"
    assert config.model_id == "deepseek-v3.2"
    assert config.pi_version == "0.73.1"
    assert config.verifier_backend == "codex"
    assert config.verifier_model == "gpt-5.6-terra"
    assert config.verifier_reasoning == "medium"
    assert config.output_root == Path("out/development")
