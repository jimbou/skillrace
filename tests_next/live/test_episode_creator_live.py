from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import uuid

import pytest

from skillrace_next.methods.episodes import (
    create_episodes,
    project_trace,
    target_episode_count,
    validate_episodes,
)
from skillrace_next.records import ExperimentConfig, RunRecord
from skillrace_next.storage import atomic_write_json, file_hash


pytestmark = pytest.mark.live


def unique_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def latest_same_track_traces(model: str, count: int) -> list[Path]:
    model_root = Path("out/live-contracts/skillrace-ten-seed") / model
    campaigns = sorted(
        (path for path in model_root.iterdir() if path.is_dir()), reverse=True
    ) if model_root.is_dir() else []
    for campaign in campaigns:
        candidates: list[Path] = []
        run_directories = sorted(
            (path for path in (campaign / "runs").iterdir() if path.is_dir()),
            reverse=True,
        ) if (campaign / "runs").is_dir() else []
        for run_directory in run_directories:
            execution = run_directory / "execution"
            record_path = execution / "run.json"
            receipt_path = execution / "runtime" / "provider.json"
            trace_path = execution / "runtime" / "trace.jsonl"
            if not all(path.is_file() for path in (record_path, receipt_path, trace_path)):
                continue
            record = json.loads(record_path.read_text(encoding="utf-8"))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (
                record.get("termination_status") == "completed"
                and record.get("model_id") == model
                and receipt.get("provider") == "lab"
                and receipt.get("model") == model
            ):
                candidates.append(execution)
        if len(candidates) >= count:
            return candidates[:count]
    pytest.fail(f"{count} completed same-campaign weak traces are required for {model}")


def copy_trace_and_source_receipt(source: Path, evidence: Path) -> Path:
    input_directory = evidence / "input"
    input_directory.mkdir(parents=True)
    trace_path = input_directory / "trace.jsonl"
    receipt_path = input_directory / "source-provider.json"
    run_path = input_directory / "source-run.json"
    shutil.copy2(source / "runtime" / "trace.jsonl", trace_path)
    shutil.copy2(source / "runtime" / "provider.json", receipt_path)
    shutil.copy2(source / "run.json", run_path)
    atomic_write_json(
        evidence / "source.json",
        {
            "schema": "skillrace-live-episode-source/2",
            "source_execution": str(source),
            "trace_hash": file_hash(trace_path),
            "provider_receipt_hash": file_hash(receipt_path),
            "run_record_hash": file_hash(run_path),
        },
    )
    return trace_path


def run_record_from_saved_trace(
    source: Path, trace_path: Path, model: str
) -> RunRecord:
    value = json.loads((source / "run.json").read_text(encoding="utf-8"))
    return RunRecord(
        run_id=value["run_id"],
        test_id=value["test_id"],
        skill_id=value["skill_id"],
        skill_version_id=value["skill_version_id"],
        method=value["method"],
        model_id=model,
        budget=value["budget"],
        container_id=value["container_id"],
        image_id=value["image_id"],
        started_at=value["started_at"],
        ended_at=value["ended_at"],
        termination_status=value["termination_status"],
        artifact_path=Path(value["artifact_path"]),
        artifact_hash=value["artifact_hash"],
        trace_path=trace_path,
        tool_log_path=Path(value["tool_log_path"]),
        stdout_path=Path(value["stdout_path"]),
        stderr_path=Path(value["stderr_path"]),
        provider_receipt_paths=(source / "runtime" / "provider.json",),
        cost_totals=dict(value["cost_totals"]),
    )


def live_config(evidence: Path, model: str) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id=f"live-episode-{model}",
        part="part1",
        methods=("skillrace",),
        replicate_count=1,
        provider="lab",
        model_id=model,
        pi_version="0.73.1",
        role_budgets={"segmenter": 8},
        verifier_backend="codex",
        verifier_command=("codex", "exec"),
        verifier_model="gpt-5.6-terra",
        verifier_reasoning="medium",
        docker_image="skillrace-next/task-fixture:test",
        resource_limits={"cpus": "1", "memory_mb": 512},
        network_policy="host",
        timeouts={
            "provider": 600,
            "pi": 600,
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


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "qwen3.6-flash"])
@pytest.mark.parametrize("source_index", [0, 1])
def test_real_pi_segments_same_track_weak_agent_trace(
    model: str, source_index: int, live_evidence_root: Path
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the live episode contract")
    source = latest_same_track_traces(model, count=2)[source_index]
    evidence = live_evidence_root / "episode-creator" / model / unique_run_id()
    trace_path = copy_trace_and_source_receipt(source, evidence)
    run = run_record_from_saved_trace(source, trace_path, model)

    episodes, receipt_path = create_episodes(
        run, live_config(evidence, model), evidence / "episodes"
    )

    _, calls = project_trace(trace_path)
    assert 0 < len(episodes) <= 2 * target_episode_count(len(calls))
    assert validate_episodes(episodes, trace_path) == episodes
    assert all(episode["outcome"].strip() for episode in episodes)
    assert all(
        episode["opening_reasoning"]
        == calls[episode["start_call"] - 1]["reasoning"]
        for episode in episodes
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["provider"] == "lab"
    assert receipt["model"] == model
    assert receipt["status"] == "completed"
    assert receipt["usage"]["total_tokens"] > 0
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
