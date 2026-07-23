from copy import deepcopy
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import uuid

import pytest

from skillrace_next.methods.episodes import create_episodes
from skillrace_next.methods.reasoning_tree import (
    empty_tree,
    merge_episodes,
    validate_tree,
)
from skillrace_next.records import ExperimentConfig, RunRecord
from skillrace_next.storage import atomic_write_json, file_hash


pytestmark = pytest.mark.live

PILOT_ROOT = Path(
    "out/live-contracts/pilot-v4/deepseek-v4-flash/part1"
)
JS_SOURCES = (
    PILOT_ROOT
    / "js-feature/replicates/0001/campaign/methods/skillrace/runs/0/execution",
    PILOT_ROOT
    / "js-feature/replicates/0001/campaign/methods/skillrace/runs/1/execution",
    PILOT_ROOT
    / "js-feature/replicates/0001/campaign/methods/verigrey/runs/0/execution",
    PILOT_ROOT
    / "js-feature/replicates/0001/campaign/methods/random/runs/0/execution",
)
CSV_SOURCES = (
    PILOT_ROOT
    / "csv-workbench/replicates/0001/campaign/methods/random/runs/0/execution",
    PILOT_ROOT
    / "csv-workbench/replicates/0001/campaign/methods/random/runs/1/execution",
)


def unique_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def live_config(evidence: Path, model: str) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id=f"live-real-skill-tree-{model}",
        part="part1",
        methods=("skillrace",),
        replicate_count=1,
        provider="lab",
        model_id=model,
        pi_version="0.73.1",
        role_budgets={"segmenter": 8, "tree_alignment": 8},
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
        iteration_budget=6,
        live=True,
        output_root=evidence,
        heldout_repetitions=1,
    )


def copied_run_record(source: Path, trace_path: Path) -> RunRecord:
    value = json.loads((source / "run.json").read_text(encoding="utf-8"))
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
        provider_receipt_paths=(source / "runtime" / "provider.json",),
        cost_totals=dict(value["cost_totals"]),
    )


def copy_source_evidence(source: Path, destination: Path) -> Path:
    input_directory = destination / "input"
    input_directory.mkdir(parents=True)
    trace_path = input_directory / "trace.jsonl"
    shutil.copy2(source / "runtime" / "trace.jsonl", trace_path)
    shutil.copy2(source / "runtime" / "provider.json", input_directory / "provider.json")
    shutil.copy2(source / "run.json", input_directory / "run.json")
    verifier_input = source.parent / "checks" / "verifier" / "input"
    for relative in ("prompt.txt", "nl_checks.json"):
        if (verifier_input / relative).is_file():
            shutil.copy2(verifier_input / relative, input_directory / relative)
    if (verifier_input / "skill" / "SKILL.md").is_file():
        (input_directory / "skill").mkdir()
        shutil.copy2(
            verifier_input / "skill" / "SKILL.md",
            input_directory / "skill" / "SKILL.md",
        )
    results_path = source.parent / "checks" / "results" / "check_results.json"
    if results_path.is_file():
        shutil.copy2(results_path, input_directory / "check_results.json")
    atomic_write_json(
        destination / "source.json",
        {
            "schema": "skillrace-real-skill-tree-source/1",
            "source_execution": str(source),
            "trace_hash": file_hash(trace_path),
            "run_hash": file_hash(input_directory / "run.json"),
            "provider_hash": file_hash(input_directory / "provider.json"),
        },
    )
    return trace_path


def segment_source(
    source: Path,
    destination: Path,
    config: ExperimentConfig,
) -> tuple[str, list[dict], list[dict[str, str]]]:
    if not source.is_dir():
        pytest.fail(f"required immutable real-skill execution is missing: {source}")
    trace_path = copy_source_evidence(source, destination)
    run = copied_run_record(source, trace_path)
    episodes, _ = create_episodes(run, config, destination / "episodes")
    results_path = destination / "input" / "check_results.json"
    failures: list[dict[str, str]] = []
    if results_path.is_file():
        results = json.loads(results_path.read_text(encoding="utf-8"))
        failures = [
            {
                "failure_id": result["check_id"],
                "episode_id": episodes[-1]["episode_id"],
            }
            for result in results["results"]
            if result["status"] == "fail"
        ]
    return run.run_id, episodes, failures


def fold_lines(
    lines: list[tuple[str, list[dict], list[dict[str, str]]]],
    config: ExperimentConfig,
    output: Path,
) -> tuple[dict, dict, dict, dict]:
    tree = empty_tree()
    cache: dict = {}
    seed_tree: dict | None = None
    two_line_cache: dict | None = None
    expected_members: set[tuple[str, str]] = set()
    for index, (run_id, episodes, failures) in enumerate(lines):
        expected_members.update(
            (run_id, episode["episode_id"]) for episode in episodes
        )
        tree, cache = merge_episodes(
            tree,
            episodes,
            run_id,
            failures,
            cache,
            config,
            output / "merges" / f"{index:02d}",
            run_meta={"source": f"inputs/{index:02d}"},
        )
        if index == 0:
            seed_tree = deepcopy(tree)
        if index == 1:
            two_line_cache = deepcopy(cache)
    assert seed_tree is not None and two_line_cache is not None
    actual_members = {
        (member["run_id"], member["episode_id"])
        for node in tree["nodes"].values()
        for member in node["members"]
    }
    assert actual_members == expected_members
    return validate_tree(tree), cache, seed_tree, two_line_cache


def node_for_member(tree: dict, run_id: str, episode_id: str) -> str:
    found = [
        node_id
        for node_id, node in tree["nodes"].items()
        if any(
            member["run_id"] == run_id
            and member["episode_id"] == episode_id
            for member in node["members"]
        )
    ]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_real_pi_builds_conservative_trees_from_real_study_skills(
    model: str, live_evidence_root: Path
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the live tree merger")
    evidence = live_evidence_root / "tree-merger" / model / unique_run_id()
    evidence.mkdir(parents=True)
    config = live_config(evidence, model)

    js_lines = [
        segment_source(source, evidence / "js" / "inputs" / f"{index:02d}", config)
        for index, source in enumerate(JS_SOURCES)
    ]
    csv_lines = [
        segment_source(source, evidence / "csv" / "inputs" / f"{index:02d}", config)
        for index, source in enumerate(CSV_SOURCES)
    ]
    js_tree, js_cache, seed_tree, two_line_cache = fold_lines(
        js_lines, config, evidence / "js"
    )
    csv_tree, csv_cache, _, _ = fold_lines(
        csv_lines, config, evidence / "csv"
    )

    deep_a, deep_b, missing, kebab = js_lines
    deep_a_impl = node_for_member(js_tree, deep_a[0], "episode-2")
    deep_b_impl = node_for_member(js_tree, deep_b[0], "episode-2")
    missing_impl = node_for_member(js_tree, missing[0], "episode-2")
    kebab_impl = node_for_member(js_tree, kebab[0], "episode-2")
    assert deep_a_impl == deep_b_impl
    assert len({deep_a_impl, missing_impl, kebab_impl}) == 3
    assert "deepclone" in js_tree["nodes"][deep_a_impl]["purpose"].lower()
    assert "findmissing" in js_tree["nodes"][missing_impl]["purpose"].lower()
    assert "kebab" in js_tree["nodes"][kebab_impl]["purpose"].lower()

    csv_a, csv_b = csv_lines
    csv_a_create = node_for_member(csv_tree, csv_a[0], "episode-1")
    csv_b_create = node_for_member(csv_tree, csv_b[0], "episode-1")
    assert csv_a_create == csv_b_create
    assert "sales.csv" in csv_tree["nodes"][csv_a_create]["purpose"].lower()

    all_failure_ids = {
        failure["failure_id"]
        for _, _, failures in js_lines + csv_lines
        for failure in failures
    }
    tree_failure_ids = {
        failure_id
        for tree in (js_tree, csv_tree)
        for node in tree["nodes"].values()
        for failure_id in node["failure_ids"]
    }
    assert tree_failure_ids == all_failure_ids

    def forbidden_pi(request) -> None:
        raise AssertionError("a repeated judgment must use the populated real cache")

    replay_tree, replay_cache = merge_episodes(
        seed_tree,
        deep_b[1],
        "cache-replay",
        [],
        two_line_cache,
        config,
        evidence / "cache-replay",
        pi_runner=forbidden_pi,
    )
    assert replay_cache == two_line_cache
    assert len(replay_tree["runs"]) == 2

    judgment_records = list(evidence.glob("**/judgment.json"))
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

    atomic_write_json(evidence / "final-js-tree.json", js_tree)
    atomic_write_json(evidence / "final-js-cache.json", js_cache)
    atomic_write_json(evidence / "final-csv-tree.json", csv_tree)
    atomic_write_json(evidence / "final-csv-cache.json", csv_cache)
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
