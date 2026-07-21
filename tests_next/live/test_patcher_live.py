from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import uuid

import pytest

from skillrace_next.pipeline.stages import build_patch_evidence, patch_skill
from skillrace_next.records import RunRecord, SkillVersion, TestCase as CaseRecord
from skillrace_next.runtime.docker import ContainerSpec, start_task_container
from skillrace_next.storage import atomic_write_json, tree_hash
from skillrace_next.verification.codex import author_checks
from skillrace_next.verification.executor import execute_checks
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


def preserved_deepseek_environment_failure() -> Path:
    root = Path("out/live-contracts/patcher/deepseek-v4-flash")
    for candidate in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
        case_path = candidate / "test-case.json"
        run_path = candidate / "weak-run" / "run.json"
        results_path = candidate / "check-results" / "check_results.json"
        if not (case_path.is_file() and run_path.is_file() and results_path.is_file()):
            continue
        case = json.loads(case_path.read_text(encoding="utf-8"))
        run = json.loads(run_path.read_text(encoding="utf-8"))
        results = json.loads(results_path.read_text(encoding="utf-8"))
        if (
            case.get("test_id") == "live-missing-node-launcher"
            and run.get("model_id") == "deepseek-v4-flash"
            and run.get("termination_status") == "completed"
            and results.get("artifact_unchanged") is True
            and any(
                item.get("exit_code") == 127
                and item.get("status") == "inconclusive"
                for item in results.get("results", [])
            )
        ):
            return candidate
    pytest.fail("the preserved DeepSeek environment-launch failure is required")


def test_real_deepseek_patches_preserved_codex_and_docker_environment_failure(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the real patcher contract")
    source = preserved_deepseek_environment_failure()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "patcher" / "deepseek-v4-flash" / run_id
    evidence.mkdir(parents=True)
    config = replace(
        live_config(evidence, {"weak_agent": 4, "patcher": 10}),
        experiment_id="live-environment-patcher",
        methods=("random",),
        network_policy="host",
        provider="lab",
        model_id="deepseek-v4-flash",
    )

    shutil.copytree(source / "skill", evidence / "skill")
    shutil.copy2(source / "skill-receipt.json", evidence / "skill-receipt.json")
    shutil.copytree(source / "test", evidence / "test")
    shutil.copytree(source / "weak-run", evidence / "weak-run")
    skill = SkillVersion(
        skill_id="live-environment-launch-repair",
        version_id="S0",
        parent_version_id=None,
        directory_path=evidence / "skill",
        tree_hash=tree_hash(evidence / "skill"),
        creation_role="fixture",
        model_id="deepseek-v4-flash",
        receipt_path=evidence / "skill-receipt.json",
    )
    source_test = CaseRecord.from_dict(
        json.loads((source / "test-case.json").read_text(encoding="utf-8"))
    )
    test = replace(
        source_test,
        prompt_path=evidence / "test" / "prompt.txt",
        environment_directory=evidence / "test" / "environment",
        nl_check_path=evidence / "test" / "nl_checks.json",
        proposal_receipt=evidence / "test" / "proposal.json",
    )
    atomic_write_json(evidence / "test-case.json", test.to_dict())
    source_run = RunRecord.from_dict(
        json.loads((source / "weak-run" / "run.json").read_text(encoding="utf-8"))
    )
    run = replace(
        source_run,
        artifact_path=evidence / "weak-run" / "artifact",
        trace_path=evidence / "weak-run" / "runtime" / "trace.jsonl",
        tool_log_path=evidence / "weak-run" / "runtime" / "tool_outputs.jsonl",
        stdout_path=evidence / "weak-run" / "runtime" / "stdout.txt",
        stderr_path=evidence / "weak-run" / "runtime" / "stderr.txt",
        provider_receipt_paths=(evidence / "weak-run" / "runtime" / "provider.json",),
    )
    atomic_write_json(evidence / "weak-run" / "run.json", run.to_dict())

    workspace = evidence / "verifier"
    verifier_input = workspace / "input"
    (verifier_input / "skill").mkdir(parents=True)
    (verifier_input / "environment").mkdir()
    (workspace / "output").mkdir(parents=True)
    shutil.copy2("skillrace_next/verification/GUIDE.md", workspace / "GUIDE.md")
    shutil.copy2(skill.directory_path / "SKILL.md", verifier_input / "skill" / "SKILL.md")
    shutil.copy2(test.prompt_path, verifier_input / "prompt.txt")
    shutil.copytree(test.environment_directory, verifier_input / "environment", dirs_exist_ok=True)
    shutil.copytree(run.artifact_path, verifier_input / "artifact")
    shutil.copy2(run.trace_path, verifier_input / "trace.jsonl")
    shutil.copy2(run.tool_log_path, verifier_input / "tool_outputs.jsonl")
    atomic_write_json(verifier_input / "run.json", run.to_dict())
    shutil.copy2(test.nl_check_path, verifier_input / "nl_checks.json")

    bundle = author_checks(workspace, config)
    running = start_task_container(
        ContainerSpec(
            name="skillrace-preserved-check-" + uuid.uuid4().hex[:12],
            image=test.container_image_id,
            image_id=test.container_image_id,
            mounts=((run.artifact_path, "/workspace", "ro"),),
            network="none",
            cpus=str(config.resource_limits["cpus"]),
            memory=f"{config.resource_limits['memory_mb']}m",
            working_directory="/workspace",
            user="0:0",
        )
    )
    results = execute_checks(running, run.artifact_path, bundle, evidence / "check-results")
    assert any(
        item["property_id"] == "P1" and item["status"] == "fail"
        for item in results.results
    )
    assert all(
        item["status"] == "pass"
        for item in results.results
        if item["property_id"] == "P2"
    )
    patch_evidence, patch_evidence_hash = build_patch_evidence(
        "random", {}, skill, test, run, bundle, results, evidence / "patch-evidence"
    )
    attempt = patch_skill(skill, patch_evidence, "random", config, evidence / "patch")

    assert attempt.patch_status == "patched"
    assert attempt.evidence_bundle_hash == patch_evidence_hash
    assert attempt.model_id == "deepseek-v4-flash"
    receipt = json.loads(attempt.cost_receipt_path.read_text(encoding="utf-8"))
    assert receipt["provider"] == "lab"
    assert receipt["model"] == "deepseek-v4-flash"
    assert receipt["max_turns"] == 10
    assert receipt["status"] == "completed"
    assert receipt["usage"]["total_tokens"] > 0
    candidate = evidence / "patch" / "candidate"
    assert tree_hash(candidate) == attempt.candidate_skill_hash
    candidate_text = (candidate / "SKILL.md").read_text(encoding="utf-8").lower()
    assert "symlink" in candidate_text or "symbolic link" in candidate_text
    assert "absolute" in candidate_text
    assert {path.relative_to(candidate).as_posix() for path in candidate.rglob("*") if path.is_file()} == {"SKILL.md"}
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
