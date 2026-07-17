from dataclasses import replace
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from skillrace_next.pipeline import stages
from skillrace_next.pipeline.stages import run_agent, validate_nl_checks, validate_test
from skillrace_next.records import ExperimentConfig, SkillVersion, TestCase as SkillTestCase
from skillrace_next.runtime.docker import ExecResult, RunningContainer
from skillrace_next.storage import file_hash, tree_hash


def config_for(root: Path) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id="test-validation",
        part="part1",
        methods=("random",),
        replicate_count=1,
        provider="yunwu",
        model_id="deepseek-v3.2",
        pi_version="0.73.1",
        role_budgets={"proposer": 4, "weak_agent": 4, "patcher": 6},
        verifier_backend="codex",
        verifier_command=("codex", "exec"),
        verifier_model="gpt-5.6-terra",
        verifier_reasoning="medium",
        docker_image="skillrace-next/task-fixture:test",
        resource_limits={"cpus": "1", "memory_mb": 512},
        network_policy="host",
        timeouts={
            "provider": 60,
            "pi": 180,
            "docker": 180,
            "codex": 300,
            "check": 60,
            "patch": 300,
        },
        suite_path=root,
        scenario_path=root,
        iteration_budget=2,
        live=False,
        output_root=root / "out",
        heldout_repetitions=1,
    )


def pending_test(root: Path) -> SkillTestCase:
    case = root / "case"
    environment = case / "environment"
    environment.mkdir(parents=True)
    prompt = case / "prompt.txt"
    prompt.write_text("Create result.txt containing ok.\n", encoding="utf-8")
    checks = case / "nl_checks.json"
    checks.write_text(
        json.dumps(
            [
                {"property_id": "P1", "description": "result.txt exists"},
                {"property_id": "P2", "description": "result.txt contains ok"},
            ]
        ),
        encoding="utf-8",
    )
    (environment / "Dockerfile").write_text(
        "FROM skillrace-next/task-fixture:test\n", encoding="utf-8"
    )
    (environment / "sanity.json").write_text(
        json.dumps({"status": "pass"}), encoding="utf-8"
    )
    receipt = case / "proposal.json"
    receipt.write_text("{}\n", encoding="utf-8")
    return SkillTestCase(
        test_id="test-1",
        prompt_path=prompt,
        prompt_hash=file_hash(prompt),
        environment_directory=environment,
        environment_hash=tree_hash(environment),
        nl_check_path=checks,
        nl_check_hash=file_hash(checks),
        origin_method="random",
        proposal_receipt=receipt,
        validation_status="pending",
        validation_diagnostic="",
        container_image_id="",
    )


def successful_build(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, "sha256:validated-image\n", "")


def test_validate_nl_checks_preserves_order_and_requires_unique_property_ids(
    tmp_path: Path,
) -> None:
    test = pending_test(tmp_path)

    checks = validate_nl_checks(test.nl_check_path)

    assert [check["property_id"] for check in checks] == ["P1", "P2"]
    duplicate = [checks[0], checks[0]]
    test.nl_check_path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        validate_nl_checks(test.nl_check_path)


def test_validate_test_builds_once_and_returns_validated_image(tmp_path: Path) -> None:
    test = pending_test(tmp_path)
    commands: list[list[str]] = []

    def recording_build(
        command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return successful_build(command, **kwargs)

    validated = validate_test(test, config_for(tmp_path), recording_build)

    assert validated.validation_status == "valid"
    assert validated.validation_diagnostic == "validated"
    assert validated.container_image_id == "sha256:validated-image"
    assert len(commands) == 1
    assert commands[0][:3] == ["docker", "build", "-q"]


@pytest.mark.parametrize(
    "invalidator",
    [
        lambda test, root: test.prompt_path.unlink(),
        lambda test, root: test.nl_check_path.write_text(
            '[{"property_id":"bad id","description":"bad"}]', encoding="utf-8"
        ),
        lambda test, root: (test.environment_directory / "sanity.json").write_text(
            '{"status":"fail"}', encoding="utf-8"
        ),
    ],
    ids=("missing-file", "malformed-property-id", "invalid-sanity"),
)
def test_validate_test_returns_invalid_test_for_bad_inputs(
    tmp_path: Path, invalidator: Any
) -> None:
    test = pending_test(tmp_path)
    invalidator(test, tmp_path)

    result = validate_test(test, config_for(tmp_path), successful_build)

    assert result.validation_status == "invalid_test"
    assert result.container_image_id == ""


def test_validate_test_rejects_path_escaping_suite_root(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    test = pending_test(suite)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    test = replace(test, prompt_path=outside, prompt_hash=file_hash(outside))

    result = validate_test(test, config_for(suite), successful_build)

    assert result.validation_status == "invalid_test"
    assert "outside" in result.validation_diagnostic


def test_validate_test_returns_invalid_test_when_docker_build_fails(tmp_path: Path) -> None:
    test = pending_test(tmp_path)

    def failed_build(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "build failed")

    result = validate_test(test, config_for(tmp_path), failed_build)

    assert result.validation_status == "invalid_test"
    assert "Docker build failed" in result.validation_diagnostic


def test_run_agent_reuses_validated_image_and_returns_live_container_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    test = replace(
        pending_test(tmp_path),
        validation_status="valid",
        validation_diagnostic="validated",
        container_image_id="sha256:validated-image",
    )
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test skill\n", encoding="utf-8")
    receipt = tmp_path / "skill.json"
    receipt.write_text("{}\n", encoding="utf-8")
    skill = SkillVersion(
        skill_id="skill-1",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v3.2",
        receipt_path=receipt,
    )
    output = tmp_path / "run"
    captured: dict[str, Any] = {}

    def fake_start(spec: Any) -> RunningContainer:
        captured["spec"] = spec
        return RunningContainer("container-1", spec.name, spec.image_id)

    def fake_exec(container: RunningContainer, argv: list[str], timeout_seconds: int) -> ExecResult:
        captured["argv"] = argv
        captured["timeout"] = timeout_seconds
        artifact = output / "artifact"
        evidence = output / "runtime"
        (artifact / "result.txt").write_text("agent output\n", encoding="utf-8")
        (evidence / "trace.jsonl").write_text(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "model": "deepseek-v3.2",
                        "usage": {"input": 10, "output": 5, "totalTokens": 15},
                        "content": [],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return ExecResult(tuple(argv), 0, "done\n", "", 0.1, False)

    monkeypatch.setattr(stages, "start_task_container", fake_start)
    monkeypatch.setattr(stages, "exec_task", fake_exec)

    record = run_agent(skill, test, config_for(tmp_path), output)

    assert captured["spec"].image == "sha256:validated-image"
    assert captured["spec"].image_id == "sha256:validated-image"
    model_index = captured["argv"].index("--model")
    assert captured["argv"][model_index + 1] == "deepseek-v3.2"
    assert record.container_id == "container-1"
    assert record.image_id == "sha256:validated-image"
    assert record.termination_status == "completed"
    assert record.artifact_path.joinpath("result.txt").is_file()
    assert record.cost_totals["input_tokens"] == 10


def test_run_agent_routes_lab_provider_and_upstream_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    test = replace(
        pending_test(tmp_path),
        validation_status="valid",
        validation_diagnostic="validated",
        container_image_id="sha256:validated-image",
    )
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test skill\n", encoding="utf-8")
    receipt = tmp_path / "skill.json"
    receipt.write_text("{}\n", encoding="utf-8")
    skill = SkillVersion(
        skill_id="skill-1",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="qwen3.6-flash",
        receipt_path=receipt,
    )
    config = replace(
        config_for(tmp_path), provider="lab", model_id="qwen3.6-flash"
    )
    output = tmp_path / "run"
    captured: dict[str, Any] = {}

    def fake_start(spec: Any) -> RunningContainer:
        captured["spec"] = spec
        return RunningContainer("container-1", spec.name, spec.image_id)

    def fake_exec(
        container: RunningContainer, argv: list[str], timeout_seconds: int
    ) -> ExecResult:
        captured["argv"] = argv
        (output / "artifact" / "result.txt").write_text("ok\n", encoding="utf-8")
        (output / "runtime" / "trace.jsonl").write_text("", encoding="utf-8")
        return ExecResult(tuple(argv), 0, "", "", 0.1, False)

    monkeypatch.setattr(stages, "start_task_container", fake_start)
    monkeypatch.setattr(stages, "exec_task", fake_exec)

    record = run_agent(skill, test, config, output)

    spec = captured["spec"]
    assert spec.environment == ("LAB_KEY_UNLIMITED",)
    assert any(
        destination == "/root/.pi/agent/models.json" and mode == "ro"
        for _, destination, mode in spec.mounts
    )
    assert captured["argv"][captured["argv"].index("--provider") + 1] == "lab"
    assert (
        captured["argv"][captured["argv"].index("--model") + 1]
        == "ali/qwen3.6-flash"
    )
    assert len(record.provider_receipt_paths) == 1
    provider_receipt = json.loads(
        record.provider_receipt_paths[0].read_text(encoding="utf-8")
    )
    assert provider_receipt["qualified_model"] == "lab/qwen3.6-flash"
    assert provider_receipt["upstream_model"] == "ali/qwen3.6-flash"
