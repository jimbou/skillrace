from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import uuid

import pytest

from skillrace_next.runtime.pi import (
    PiRequest,
    direct_provider_preflight,
    run_pi,
)
from skillrace_next.pipeline.stages import generate_base_skill
from skillrace_next.storage import tree_hash
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_real_lab_direct_and_pi_tool_contract(
    model: str, live_evidence_root: Path
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the live contract")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "lab-provider" / model / run_id
    direct_dir = evidence / "direct"
    pi_dir = evidence / "pi"
    workspace = evidence / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("lab-live-probe\n", encoding="utf-8")
    prompt = evidence / "prompt.txt"
    prompt.write_text(
        "Use read to read /workspace/input.txt. Then use write to create "
        "/workspace/output.txt containing exactly LAB_PI_TOOL_OK followed by one newline. "
        "Perform both tool calls and stop.\n",
        encoding="utf-8",
    )

    probe = direct_provider_preflight("lab", model, direct_dir)
    assert probe.status == "completed", probe.receipt_path
    assert "SKILLRACE_PREFLIGHT_OK" in probe.content
    assert probe.usage

    result = run_pi(
        PiRequest(
            operation_id=f"lab-provider.{model}.{run_id}",
            provider="lab",
            model=model,
            prompt_path=prompt,
            output_dir=pi_dir,
            image="skillrace/pi-base:0.73.1-deepseek-v3.2",
            allowed_tools=("read", "write"),
            max_turns=4,
            timeout_seconds=240,
            mounts=((workspace, "/workspace", "rw"),),
        )
    )

    assert result.status == "completed", result.receipt_path
    assert result.trace_path.is_file()
    assert result.usage.get("input_tokens", 0) > 0
    assert result.usage.get("output_tokens", 0) > 0
    assert result.usage.get("turns", 0) <= 4
    assert (workspace / "output.txt").read_text(encoding="utf-8") == "LAB_PI_TOOL_OK\n"
    events_path = pi_dir / "accounting" / "tool-events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    tools = [event.get("tool") for event in events if event.get("type") == "tool_call"]
    assert "read" in tools
    assert "write" in tools

    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_real_lab_model_generates_base_skill_as_pipeline_cheap_model(
    model: str, live_evidence_root: Path
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the live contract")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "lab-cheap-model" / model / run_id
    evidence.mkdir(parents=True)
    scenario = evidence / "scenario.md"
    scenario.write_text(
        "Build dependable small command-line tools that read structured local text files, "
        "perform a requested deterministic transformation, write the exact requested "
        "artifact, and verify the observable result.\n",
        encoding="utf-8",
    )
    base_config = live_config(evidence, {"skill_generator": 6})
    config = replace(
        base_config,
        experiment_id=f"lab-cheap-model-{model}",
        part="part2",
        methods=("random", "verigrey", "skillrace"),
        provider="lab",
        model_id=model,
        timeouts={**base_config.timeouts, "pi": 240},
    )

    skill = generate_base_skill(scenario, config, evidence / "generated")

    assert skill.version_id == "S0"
    assert skill.model_id == model
    assert skill.tree_hash == tree_hash(skill.directory_path)
    skill_text = (skill.directory_path / "SKILL.md").read_text(encoding="utf-8")
    assert skill_text.startswith("---\n")
    assert len(skill_text) > 200
    for method in config.methods:
        copied = evidence / "generated" / "methods" / method / "SKILL.md"
        assert copied.read_text(encoding="utf-8") == skill_text
    receipt = json.loads(skill.receipt_path.read_text(encoding="utf-8"))
    assert receipt["provider"] == "lab"
    assert receipt["model"] == model
    assert receipt["qualified_model"] == f"lab/{model}"
    assert receipt["status"] == "completed"
    assert receipt["usage"]["total_tokens"] > 0
    assert receipt["estimated_cost_usd"] != "unpriced"
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
