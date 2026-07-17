from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from skillrace_next.pipeline.stages import (
    build_patch_evidence,
    patch_skill,
    run_agent,
    validate_test,
)
from skillrace_next.records import SkillVersion, TestCase as CaseRecord
from skillrace_next.runtime.docker import RunningContainer
from skillrace_next.storage import atomic_write_json, file_hash, tree_hash
from skillrace_next.verification.codex import author_checks
from skillrace_next.verification.executor import execute_checks
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


def test_real_yunwu_patches_one_skill_from_real_codex_and_docker_failure(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("yunwu_key")
    if not secret:
        pytest.skip("yunwu_key is required for the real patcher contract")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "patcher" / run_id
    evidence.mkdir(parents=True)
    config = replace(
        live_config(evidence, {"weak_agent": 4, "patcher": 6}),
        experiment_id="live-patcher",
        methods=("random",),
        network_policy="host",
    )
    base_image_id = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", config.docker_image],
        check=True,
        text=True,
        capture_output=True,
        timeout=config.timeouts["docker"],
    ).stdout.strip()
    atomic_write_json(
        evidence / "docker-preflight.json",
        {"base_image": config.docker_image, "base_image_id": base_image_id},
    )
    skill_dir = evidence / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: median-calculation\ndescription: Calculate the median of numeric values.\n---\n"
        "# Median calculation\nSort the numbers. For an odd count, return the middle "
        "number. For an even count, return the lower of the two middle numbers; this is "
        "the required median convention. Write only that number.\n",
        encoding="utf-8",
    )
    skill_receipt = evidence / "skill-receipt.json"
    atomic_write_json(skill_receipt, {"source": "deliberately deficient live S0"})
    skill = SkillVersion(
        skill_id="live-median-calculation",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v3.2",
        receipt_path=skill_receipt,
    )
    case = evidence / "test"
    environment = case / "environment"
    environment.mkdir(parents=True)
    (environment / "values.txt").write_text("1\n2\n100\n200\n", encoding="utf-8")
    (environment / "Dockerfile").write_text(
        f"FROM {config.docker_image}\nCOPY values.txt /input/values.txt\nWORKDIR /workspace\n",
        encoding="utf-8",
    )
    atomic_write_json(environment / "sanity.json", {"status": "pass"})
    prompt = case / "prompt.txt"
    prompt.write_text(
        "Using the installed skill, read the integers in /input/values.txt, calculate their "
        "median, and write only the resulting number followed by a newline to "
        "/workspace/result.txt.\n",
        encoding="utf-8",
    )
    nl_checks = case / "nl_checks.json"
    atomic_write_json(
        nl_checks,
        [
            {
                "property_id": "P1",
                "description": (
                    "result.txt exists and contains exactly the standard median 51 with no "
                    "other non-whitespace content."
                ),
            }
        ],
    )
    proposal = case / "proposal.json"
    atomic_write_json(proposal, {"source": "live development failure fixture"})
    test = CaseRecord(
        test_id="live-even-median",
        prompt_path=prompt,
        prompt_hash=file_hash(prompt),
        environment_directory=environment,
        environment_hash=tree_hash(environment),
        nl_check_path=nl_checks,
        nl_check_hash=file_hash(nl_checks),
        origin_method="random",
        proposal_receipt=proposal,
        validation_status="pending",
        validation_diagnostic="",
        container_image_id="",
    )
    test = validate_test(test, config)
    assert test.validation_status == "valid"
    atomic_write_json(evidence / "test-case.json", test.to_dict())

    run = run_agent(skill, test, config, evidence / "weak-run")
    assert run.termination_status == "completed"
    workspace = evidence / "verifier"
    verifier_input = workspace / "input"
    verifier_output = workspace / "output"
    (verifier_input / "skill").mkdir(parents=True)
    (verifier_input / "environment").mkdir()
    verifier_output.mkdir(parents=True)
    shutil.copy2("skillrace_next/verification/GUIDE.md", workspace / "GUIDE.md")
    shutil.copy2(skill_dir / "SKILL.md", verifier_input / "skill" / "SKILL.md")
    shutil.copy2(prompt, verifier_input / "prompt.txt")
    shutil.copytree(environment, verifier_input / "environment", dirs_exist_ok=True)
    shutil.copytree(run.artifact_path, verifier_input / "artifact")
    shutil.copy2(run.trace_path, verifier_input / "trace.jsonl")
    shutil.copy2(run.tool_log_path, verifier_input / "tool_outputs.jsonl")
    atomic_write_json(verifier_input / "run.json", run.to_dict())
    shutil.copy2(nl_checks, verifier_input / "nl_checks.json")
    bundle = author_checks(workspace, config)
    assert bundle.script_paths
    running = RunningContainer(run.container_id, "live-patcher-run", run.image_id)
    results = execute_checks(
        running,
        run.artifact_path,
        bundle,
        evidence / "check-results",
    )
    assert any(item["status"] == "fail" for item in results.results), (
        "the real weak-agent run must produce a defensible failing check"
    )
    patch_evidence, patch_evidence_hash = build_patch_evidence(
        "random",
        {},
        skill,
        test,
        run,
        bundle,
        results,
        evidence / "patch-evidence",
    )

    attempt = patch_skill(
        skill,
        patch_evidence,
        "random",
        config,
        evidence / "patch",
    )

    assert attempt.patch_status == "patched"
    assert attempt.evidence_bundle_hash == patch_evidence_hash
    assert attempt.model_id == "deepseek-v3.2"
    receipt = json.loads(attempt.cost_receipt_path.read_text(encoding="utf-8"))
    assert receipt["provider"] == "yunwu"
    assert receipt["model"] == "deepseek-v3.2"
    assert receipt["status"] == "completed"
    assert receipt["usage"]["total_tokens"] > 0
    candidate = evidence / "patch" / "candidate"
    assert tree_hash(candidate) == attempt.candidate_skill_hash
    assert (candidate / "SKILL.md").read_bytes() != (skill_dir / "SKILL.md").read_bytes()
    assert {path.relative_to(candidate).as_posix() for path in candidate.rglob("*") if path.is_file()} == {"SKILL.md"}
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
