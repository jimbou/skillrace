from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from skillrace_next.methods.random import propose_valid_test
from skillrace_next.records import ExperimentConfig, SkillVersion
from skillrace_next.storage import atomic_write_json, tree_hash


pytestmark = pytest.mark.live


@pytest.mark.parametrize("model_id", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_real_random_proposal_passes_deterministic_validation(
    live_evidence_root: Path,
    model_id: str,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the live contract")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "test-proposer" / model_id / run_id
    evidence.mkdir(parents=True)
    image = "skillrace-next/task-fixture:test"
    subprocess.run(
        [
            "docker",
            "build",
            "-q",
            "-t",
            image,
            str(Path("tests_next/fixtures/task").resolve()),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    skill_dir = evidence / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "# Exact file creation\n"
        "Follow the user's requested path and content exactly. Use the write tool, then "
        "read the file to confirm it.\n",
        encoding="utf-8",
    )
    skill_receipt = evidence / "skill-receipt.json"
    atomic_write_json(skill_receipt, {"source": "development fixture"})
    skill = SkillVersion(
        skill_id="development-file-creation",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id=model_id,
        receipt_path=skill_receipt,
    )
    config = ExperimentConfig(
        experiment_id="live-test-proposer",
        part="part1",
        methods=("random",),
        replicate_count=1,
        provider="lab",
        model_id=model_id,
        pi_version="0.73.1",
        role_budgets={"proposer": 4, "weak_agent": 4, "patcher": 6},
        verifier_backend="codex",
        verifier_command=("codex", "exec"),
        verifier_model="gpt-5.6-terra",
        verifier_reasoning="medium",
        docker_image=image,
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
        suite_path=evidence,
        scenario_path=evidence,
        iteration_budget=1,
        live=True,
        output_root=evidence,
        heldout_repetitions=1,
    )
    properties = [
        {
            "property_id": "P1",
            "description": "The task creates the requested output file.",
        },
        {
            "property_id": "P2",
            "description": "The output file contains the exact requested text.",
        },
    ]

    validated = propose_valid_test(skill, properties, config, evidence / "proposal")
    atomic_write_json(evidence / "validated-test.json", validated.to_dict())

    assert validated.validation_status == "valid", validated.validation_diagnostic
    assert validated.container_image_id.startswith("sha256:")
    assert validated.origin_method == "random"
    prompt = validated.prompt_path.read_text(encoding="utf-8")
    assert "file" in prompt.lower()
    assert "/mnt/data" not in prompt
    assert "/tmp" not in prompt
    checks = json.loads(validated.nl_check_path.read_text(encoding="utf-8"))
    assert checks == properties
    dockerfile = (validated.environment_directory / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert dockerfile.startswith(f"FROM {image}\n")
    assert "WORKDIR /workspace" in dockerfile
    proposal_receipt = json.loads(
        validated.proposal_receipt.read_text(encoding="utf-8")
    )
    assert proposal_receipt["catalog_hash"] == validated.nl_check_hash
    assert proposal_receipt["environment_hash"] == validated.environment_hash
    pi_receipt = json.loads(
        Path(proposal_receipt["pi_receipt_path"]).read_text(encoding="utf-8")
    )
    assert pi_receipt["status"] == "completed"
    assert pi_receipt["provider"] == "lab"
    assert pi_receipt["model"] == model_id
    assert pi_receipt["qualified_model"] == f"lab/{model_id}"
    assert pi_receipt["temperature"] == 1.0
    assert pi_receipt["usage"]["input_tokens"] > 0
    assert Path(pi_receipt["trace_path"]).is_file()
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
