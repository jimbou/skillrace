from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import uuid

import pytest

from skillrace_next.methods.skillrace import create_episodes, validate_episodes
from skillrace_next.records import ExperimentConfig, RunRecord
from skillrace_next.storage import atomic_write_json, tree_hash


pytestmark = pytest.mark.live


def latest_real_task_run() -> Path:
    root = Path("out/live-contracts/task-runner")
    for candidate in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
        receipt_path = candidate / "runtime" / "exec.json"
        trace_path = candidate / "runtime" / "trace.jsonl"
        if not receipt_path.is_file() or not trace_path.is_file():
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("exit_code") == 0 and receipt.get("model") == "deepseek-v3.2":
            return candidate
    pytest.fail("a successful real Yunwu task trace is required")


def test_real_yunwu_segments_saved_agent_trace_into_grounded_episodes(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("yunwu_key")
    if not secret:
        pytest.skip("yunwu_key is required for the live episode contract")
    source = latest_real_task_run()
    source_receipt = json.loads(
        (source / "runtime" / "exec.json").read_text(encoding="utf-8")
    )
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "episode-creator" / run_id
    input_dir = evidence / "input"
    input_dir.mkdir(parents=True)
    trace_path = input_dir / "trace.jsonl"
    shutil.copy2(source / "runtime" / "trace.jsonl", trace_path)
    atomic_write_json(
        evidence / "source.json",
        {
            "schema": "skillrace-live-episode-source/1",
            "source_task_run": str(source),
            "model": source_receipt["model"],
            "image_id": source_receipt["image_id"],
            "trace_hash": tree_hash(input_dir),
        },
    )
    run = RunRecord(
        run_id=f"episode-source-{source.name}",
        test_id="live-file-creation",
        skill_id="live-exact-marker",
        skill_version_id="S0",
        method="skillrace",
        model_id="deepseek-v3.2",
        budget=4,
        container_id=source_receipt["container_id"],
        image_id=source_receipt["image_id"],
        started_at="2026-07-17T00:00:00Z",
        ended_at="2026-07-17T00:00:01Z",
        termination_status="completed",
        artifact_path=source / "artifact",
        artifact_hash=tree_hash(source / "artifact"),
        trace_path=trace_path,
        tool_log_path=source / "runtime" / "tool_outputs.jsonl",
        stdout_path=source / "runtime" / "stdout.txt",
        stderr_path=source / "runtime" / "stderr.txt",
        provider_receipt_paths=(source / "runtime" / "exec.json",),
        cost_totals={},
    )
    config = ExperimentConfig(
        experiment_id="live-episode-creator",
        part="part1",
        methods=("skillrace",),
        replicate_count=1,
        provider="yunwu",
        model_id="deepseek-v3.2",
        pi_version="0.73.1",
        role_budgets={"segmenter": 4},
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
        suite_path=evidence,
        scenario_path=evidence,
        iteration_budget=1,
        live=True,
        output_root=evidence,
        heldout_repetitions=1,
    )

    episodes, receipt_path = create_episodes(run, config, evidence / "episodes")

    assert validate_episodes(episodes, trace_path) == episodes
    assert episodes
    assert all(episode["purpose"].strip() for episode in episodes)
    assert all(episode["outcome"].strip() for episode in episodes)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["provider"] == "yunwu"
    assert receipt["model"] == "deepseek-v3.2"
    assert receipt["status"] == "completed"
    assert receipt["usage"]["total_tokens"] > 0
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
