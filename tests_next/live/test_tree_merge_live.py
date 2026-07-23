from copy import deepcopy
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
)
from skillrace_next.methods.reasoning_tree import (
    empty_tree,
    merge_episodes,
    validate_tree,
)
from skillrace_next.records import ExperimentConfig, RunRecord
from skillrace_next.storage import atomic_write_json, file_hash


pytestmark = pytest.mark.live


def unique_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def live_config(evidence: Path, model: str) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id=f"live-tree-{model}",
        part="part1",
        methods=("skillrace",),
        replicate_count=1,
        provider="lab",
        model_id=model,
        pi_version="0.73.1",
        role_budgets={"segmenter": 8, "tree_alignment": 6},
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


def latest_episode_runs(model: str, count: int) -> list[Path]:
    root = Path("out/live-contracts/episode-creator") / model
    found: list[Path] = []
    for candidate in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
        episodes_path = candidate / "episodes" / "episodes.json"
        creation_path = candidate / "episodes" / "episode-creation.json"
        if not episodes_path.is_file() or not creation_path.is_file():
            continue
        creation = json.loads(creation_path.read_text(encoding="utf-8"))
        receipt_path = Path(creation["pi_receipt_path"])
        if not receipt_path.is_file():
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt.get("status") == "completed"
            and receipt.get("provider") == "lab"
            and receipt.get("model") == model
        ):
            found.append(candidate)
        if len(found) == count:
            return found
    pytest.fail(f"{count} completed episode contracts are required for {model}")


def copy_existing_episode_run(source: Path, destination: Path) -> tuple[str, list[dict]]:
    destination.mkdir(parents=True)
    episodes_path = source / "episodes" / "episodes.json"
    creation = json.loads(
        (source / "episodes" / "episode-creation.json").read_text(encoding="utf-8")
    )
    receipt_path = Path(creation["pi_receipt_path"])
    shutil.copy2(episodes_path, destination / "episodes.json")
    shutil.copy2(receipt_path, destination / "receipt.json")
    shutil.copy2(source / "input" / "source-run.json", destination / "source-run.json")
    shutil.copy2(source / "source.json", destination / "source.json")
    record = json.loads((destination / "source-run.json").read_text(encoding="utf-8"))
    episodes = json.loads((destination / "episodes.json").read_text(encoding="utf-8"))
    return record["run_id"], episodes


def additional_real_sources(model: str, excluded: set[str], count: int) -> list[Path]:
    root = Path("out/live-contracts/skillrace-ten-seed") / model
    candidates: list[tuple[int, int, int, str, Path]] = []
    for record_path in root.glob("*/runs/*/execution/run.json") if root.is_dir() else []:
        execution = record_path.parent
        trace_path = execution / "runtime" / "trace.jsonl"
        provider_path = execution / "runtime" / "provider.json"
        if str(execution) in excluded or not trace_path.is_file() or not provider_path.is_file():
            continue
        record = json.loads(record_path.read_text(encoding="utf-8"))
        provider = json.loads(provider_path.read_text(encoding="utf-8"))
        if (
            record.get("termination_status") != "completed"
            or record.get("model_id") != model
            or record.get("skill_id") != "file-check"
            or provider.get("provider") != "lab"
            or provider.get("model") != model
        ):
            continue
        try:
            _, calls = project_trace(trace_path)
        except ValueError:
            continue
        candidates.append(
            (
                target_episode_count(len(calls)),
                int(calls[0]["tool"] == "write"),
                len(calls),
                str(execution),
                execution,
            )
        )
    candidates.sort(reverse=True)
    if len(candidates) < count:
        pytest.fail(f"{count} additional completed weak traces are required for {model}")
    return [item[4] for item in candidates[:count]]


def copied_run_record(execution: Path, trace_path: Path) -> RunRecord:
    value = json.loads((execution / "run.json").read_text(encoding="utf-8"))
    return RunRecord(
        run_id=value["run_id"],
        test_id=value["test_id"],
        skill_id=value["skill_id"],
        skill_version_id=value["skill_version_id"],
        method=value["method"],
        model_id=value["model_id"],
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
        provider_receipt_paths=(execution / "runtime" / "provider.json",),
        cost_totals=dict(value["cost_totals"]),
    )


def segment_additional_source(
    source: Path,
    destination: Path,
    config: ExperimentConfig,
) -> tuple[str, list[dict]]:
    input_directory = destination / "input"
    input_directory.mkdir(parents=True)
    trace_path = input_directory / "trace.jsonl"
    shutil.copy2(source / "runtime" / "trace.jsonl", trace_path)
    shutil.copy2(source / "run.json", input_directory / "source-run.json")
    shutil.copy2(source / "runtime" / "provider.json", input_directory / "source-provider.json")
    atomic_write_json(
        destination / "source.json",
        {
            "schema": "skillrace-live-tree-source/1",
            "source_execution": str(source),
            "trace_hash": file_hash(trace_path),
        },
    )
    run = copied_run_record(source, trace_path)
    episodes, _ = create_episodes(run, config, destination / "episodes")
    return run.run_id, episodes


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_real_pi_builds_cached_contextual_tree_from_real_episode_lines(
    model: str, live_evidence_root: Path
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the live tree merger")
    evidence = live_evidence_root / "tree-merger" / model / unique_run_id()
    evidence.mkdir(parents=True)
    config = live_config(evidence, model)
    lines: list[tuple[str, list[dict]]] = []
    excluded: set[str] = set()
    for index, source in enumerate(latest_episode_runs(model, 2)):
        lines.append(
            copy_existing_episode_run(source, evidence / "inputs" / f"existing-{index}")
        )
        source_record = json.loads((source / "source.json").read_text(encoding="utf-8"))
        excluded.add(source_record["source_execution"])
    for index, source in enumerate(additional_real_sources(model, excluded, 2)):
        lines.append(
            segment_additional_source(
                source, evidence / "inputs" / f"additional-{index}", config
            )
        )

    tree = empty_tree()
    cache: dict = {}
    seed_tree: dict | None = None
    two_line_cache: dict | None = None
    expected_members: set[tuple[str, str]] = set()
    failure_id = "live-failure-second-line"
    for index, (run_id, episodes) in enumerate(lines):
        failures = (
            [{"failure_id": failure_id, "episode_id": episodes[-1]["episode_id"]}]
            if index == 1
            else []
        )
        for episode in episodes:
            expected_members.add((run_id, episode["episode_id"]))
        tree, cache = merge_episodes(
            tree,
            episodes,
            run_id,
            failures,
            cache,
            config,
            evidence / "merges" / f"{index:02d}",
            run_meta={"source": f"inputs/{index}"},
        )
        if index == 0:
            seed_tree = deepcopy(tree)
        if index == 1:
            two_line_cache = deepcopy(cache)

    assert seed_tree is not None and two_line_cache is not None
    assert validate_tree(tree) == tree
    actual_members = {
        (member["run_id"], member["episode_id"])
        for node in tree["nodes"].values()
        for member in node["members"]
    }
    assert actual_members == expected_members
    shared_nodes = [node for node in tree["nodes"].values() if len(node["runs"]) > 1]
    assert shared_nodes
    assert any(failure_id in node["failure_ids"] for node in tree["nodes"].values())

    def forbidden_pi(request) -> None:
        raise AssertionError("a repeated judgment must use the real populated cache")

    replay_tree, replay_cache = merge_episodes(
        seed_tree,
        lines[1][1],
        "cache-replay",
        [],
        two_line_cache,
        config,
        evidence / "cache-replay",
        pi_runner=forbidden_pi,
    )
    assert replay_cache == two_line_cache
    assert len(replay_tree["runs"]) == 2

    all_outcomes = {
        episode["outcome"] for _, episodes in lines for episode in episodes
    }
    same_purpose_prompts = list(
        (evidence / "merges").glob("**/judgments/same-purpose/**/prompt.txt")
    )
    assert same_purpose_prompts
    for prompt_path in same_purpose_prompts:
        prompt = prompt_path.read_text(encoding="utf-8")
        assert all(outcome not in prompt for outcome in all_outcomes)

    judgment_records = list((evidence / "merges").glob("**/judgment.json"))
    assert judgment_records
    for judgment_path in judgment_records:
        judgment = json.loads(judgment_path.read_text(encoding="utf-8"))
        receipt = json.loads(
            Path(judgment["pi_receipt_path"]).read_text(encoding="utf-8")
        )
        assert receipt["provider"] == "lab"
        assert receipt["model"] == model
        assert receipt["status"] == "completed"
        assert receipt["usage"]["total_tokens"] > 0
    atomic_write_json(evidence / "final-tree.json", tree)
    atomic_write_json(evidence / "final-cache.json", cache)
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
