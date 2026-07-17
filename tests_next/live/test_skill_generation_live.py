from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from skillrace_next.pipeline.stages import generate_base_skill
from skillrace_next.storage import atomic_write_json, tree_hash
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


def test_real_yunwu_generates_one_isolated_base_skill(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("yunwu_key")
    if not secret:
        pytest.skip("yunwu_key is required for the base-skill generation contract")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "skill-generator" / run_id
    evidence.mkdir(parents=True)
    scenario = evidence / "scenario.md"
    scenario.write_text(
        "Build dependable small command-line tools that read structured local text files, "
        "perform a requested deterministic transformation, write the exact requested "
        "artifact, and verify the observable result.\n",
        encoding="utf-8",
    )
    config = replace(
        live_config(evidence, {"skill_generator": 6}),
        experiment_id="live-base-skill",
        part="part2",
        methods=("random", "verigrey", "skillrace"),
    )
    image_id = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", config.docker_image],
        check=True,
        text=True,
        capture_output=True,
        timeout=config.timeouts["docker"],
    ).stdout.strip()
    atomic_write_json(
        evidence / "docker-preflight.json",
        {"base_image": config.docker_image, "base_image_id": image_id},
    )

    skill = generate_base_skill(scenario, config, evidence / "generated")

    assert skill.version_id == "S0"
    assert skill.parent_version_id is None
    assert skill.model_id == "deepseek-v3.2"
    assert skill.tree_hash == tree_hash(skill.directory_path)
    skill_bytes = (skill.directory_path / "SKILL.md").read_bytes()
    assert skill_bytes.startswith(b"---\n")
    assert len(skill_bytes) > 200
    for method in config.methods:
        assert (evidence / "generated" / "methods" / method / "SKILL.md").read_bytes() == skill_bytes
    receipt = json.loads(skill.receipt_path.read_text(encoding="utf-8"))
    assert receipt["provider"] == "yunwu"
    assert receipt["model"] == "deepseek-v3.2"
    assert receipt["status"] == "completed"
    assert receipt["usage"]["total_tokens"] > 0
    generation = json.loads(
        (evidence / "generated" / "generation.json").read_text(encoding="utf-8")
    )
    assert generation["trace_path"] == str(
        (evidence / "generated" / "generation" / "pi" / "trace.jsonl").resolve()
    )
    assert generation["pi_receipt_path"] == str(skill.receipt_path)
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
