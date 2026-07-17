import json
from pathlib import Path
from typing import Any

import pytest

from skillrace_next.records import (
    CheckBundle,
    CheckResults,
    ExperimentConfig,
    ImprovementStep,
    PatchAttempt,
    RunRecord,
    SkillVersion,
    TestCase as SkillTestCase,
)


def valid_config() -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id="development",
        part="part1",
        methods=("random", "verigrey", "skillrace"),
        replicate_count=1,
        provider="yunwu",
        model_id="deepseek-v3.2",
        pi_version="0.73.1",
        role_budgets={"proposer": 4, "weak_agent": 4, "patcher": 6},
        verifier_backend="codex",
        verifier_command=("codex", "exec"),
        verifier_model="gpt-5.6-terra",
        verifier_reasoning="medium",
        docker_image="skillrace-next-development:latest",
        resource_limits={"cpus": "1", "memory_mb": 512},
        network_policy="none",
        timeouts={
            "provider": 60,
            "pi": 180,
            "docker": 180,
            "codex": 300,
            "check": 60,
            "patch": 300,
        },
        suite_path=Path("tests_next/fixtures/suite"),
        scenario_path=Path("tests_next/fixtures/scenario"),
        iteration_budget=2,
        live=False,
        output_root=Path("out/development"),
        heldout_repetitions=1,
    )


def all_records() -> list[Any]:
    return [
        valid_config(),
        SkillVersion(
            skill_id="skill-1",
            version_id="S0",
            parent_version_id=None,
            directory_path=Path("skills/skill-1"),
            tree_hash="skill-hash",
            creation_role="skill_generator",
            model_id="deepseek-v3.2",
            receipt_path=Path("receipts/generation.json"),
        ),
        SkillTestCase(
            test_id="test-1",
            prompt_path=Path("test/prompt.txt"),
            prompt_hash="prompt-hash",
            environment_directory=Path("test/environment"),
            environment_hash="environment-hash",
            nl_check_path=Path("test/nl_checks.json"),
            nl_check_hash="checks-hash",
            origin_method="random",
            proposal_receipt=Path("receipts/proposal.json"),
            validation_status="valid",
            validation_diagnostic="validated",
            container_image_id="sha256:image",
        ),
        RunRecord(
            run_id="run-1",
            test_id="test-1",
            skill_id="skill-1",
            skill_version_id="S0",
            method="random",
            model_id="deepseek-v3.2",
            budget=4,
            container_id="container-1",
            image_id="sha256:image",
            started_at="2026-07-17T10:00:00Z",
            ended_at="2026-07-17T10:01:00Z",
            termination_status="completed",
            artifact_path=Path("runs/run-1/artifact"),
            artifact_hash="artifact-hash",
            trace_path=Path("runs/run-1/trace.jsonl"),
            tool_log_path=Path("runs/run-1/tool_outputs.jsonl"),
            stdout_path=Path("runs/run-1/stdout.txt"),
            stderr_path=Path("runs/run-1/stderr.txt"),
            provider_receipt_paths=(Path("runs/run-1/provider.json"),),
            cost_totals={"input_tokens": 10, "provider_credits": "unpriced"},
        ),
        CheckBundle(
            bundle_id="bundle-1",
            run_id="run-1",
            artifact_hash="artifact-hash",
            input_hashes={"skill": "skill-hash", "prompt": "prompt-hash"},
            manifest_path=Path("verification/check_manifest.json"),
            script_paths=(Path("verification/checks/P1-C1.py"),),
            codex_receipt_path=Path("verification/codex.jsonl"),
        ),
        CheckResults(
            results_id="results-1",
            run_id="run-1",
            check_bundle_hash="bundle-hash",
            artifact_hash_before="artifact-hash",
            artifact_hash_after="artifact-hash",
            artifact_unchanged=True,
            results=({"check_id": "P1-C1", "status": "pass"},),
            results_path=Path("verification/check_results.json"),
        ),
        PatchAttempt(
            patch_attempt_id="patch-1",
            input_skill_hash="skill-hash",
            evidence_bundle_hash="evidence-hash",
            method="random",
            model_id="deepseek-v3.2",
            pi_trace_path=Path("patches/patch-1/trace.jsonl"),
            cost_receipt_path=Path("patches/patch-1/cost.json"),
            candidate_skill_hash="candidate-hash",
            patch_status="completed",
            replay_path=Path("patches/patch-1/replay"),
            acceptance_status="accepted",
        ),
        ImprovementStep(
            iteration=1,
            input_skill_version_id="S0",
            test_id="test-1",
            run_id="run-1",
            check_results_id="results-1",
            patch_attempt_id="patch-1",
            decision="accepted",
            resulting_skill_version_id="S1",
            regression_results=({"test_id": "test-0", "status": "pass"},),
        ),
    ]


@pytest.mark.parametrize("record", all_records(), ids=lambda record: type(record).__name__)
def test_record_round_trip(record: Any) -> None:
    serialized = record.to_dict()

    assert serialized["schema"].endswith("/1")
    json.dumps(serialized)
    assert type(record).from_dict(serialized) == record


def test_path_fields_are_strings_in_json() -> None:
    skill = all_records()[1]

    assert isinstance(skill.to_dict()["directory_path"], str)
    assert isinstance(skill.to_dict()["receipt_path"], str)


def test_run_record_rejects_unknown_termination_status() -> None:
    values = all_records()[3].to_dict()
    values["termination_status"] = "unknown"

    with pytest.raises(ValueError, match="termination_status"):
        RunRecord.from_dict(values)
