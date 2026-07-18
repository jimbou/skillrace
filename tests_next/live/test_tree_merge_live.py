from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import uuid

import pytest

from skillrace_next.methods.skillrace import merge_episodes, validate_tree
from skillrace_next.records import ExperimentConfig
from skillrace_next.storage import atomic_write_json


pytestmark = pytest.mark.live


def latest_episode_run() -> Path:
    root = Path("out/live-contracts/episode-creator")
    for candidate in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
        episodes = candidate / "episodes" / "episodes.json"
        receipt = candidate / "episodes" / "episode-attempt-1" / "receipt.json"
        if not episodes.is_file() or not receipt.is_file():
            continue
        value = json.loads(receipt.read_text(encoding="utf-8"))
        if value.get("status") == "completed" and value.get("model") == "deepseek-v3.2":
            return candidate
    pytest.fail("a successful real Yunwu episode run is required")


def seed_tree() -> dict[str, object]:
    return {
        "schema": "skillrace-reasoning-tree/1",
        "nodes": [
            {
                "node_id": "root",
                "purpose": "root",
                "outcome": "root",
                "member_run_ids": [],
                "member_episode_ids": [],
                "reach_status": "reached",
                "failure_ids": [],
            },
            {
                "node_id": "unreached-alternative",
                "purpose": "Use a different file workflow",
                "outcome": "This alternative has not been exercised",
                "member_run_ids": [],
                "member_episode_ids": [],
                "reach_status": "unreached",
                "failure_ids": [],
            },
        ],
        "edges": [
            {
                "source_node_id": "root",
                "target_node_id": "unreached-alternative",
                "reason": "A different workflow remains available",
            }
        ],
    }


def live_config(evidence: Path, roles: dict[str, int]) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id="live-tree-merge",
        part="part1",
        methods=("skillrace",),
        replicate_count=1,
        provider="yunwu",
        model_id="deepseek-v3.2",
        pi_version="0.73.1",
        role_budgets=roles,
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


def test_real_yunwu_aligns_real_episodes_in_one_batched_tree_call(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("yunwu_key")
    if not secret:
        pytest.skip("yunwu_key is required for the live tree merger")
    source = latest_episode_run()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "tree-merger" / run_id
    evidence.mkdir(parents=True)
    episodes_path = evidence / "episodes.json"
    shutil.copy2(source / "episodes" / "episodes.json", episodes_path)
    episodes = json.loads(episodes_path.read_text(encoding="utf-8"))
    atomic_write_json(evidence / "seed-tree.json", seed_tree())
    failure = {"failure_id": "live-failure-P2", "episode_id": episodes[-1]["episode_id"]}

    merged = merge_episodes(
        seed_tree(),
        episodes,
        f"tree-source-{source.name}",
        [failure],
        live_config(evidence, {"tree_alignment": 4}),
        evidence / "merge",
    )

    assert validate_tree(merged) == merged
    assert next(node for node in merged["nodes"] if node["node_id"] == "unreached-alternative")
    memberships = {
        episode_id
        for node in merged["nodes"]
        for episode_id in node["member_episode_ids"]
    }
    assert memberships == {episode["episode_id"] for episode in episodes}
    assert any("live-failure-P2" in node["failure_ids"] for node in merged["nodes"])
    merge_receipt = json.loads((evidence / "merge" / "tree-merge.json").read_text())
    alignment_receipt = Path(merge_receipt["alignment_receipt_path"])
    receipt = json.loads(alignment_receipt.read_text(encoding="utf-8"))
    assert receipt["provider"] == "yunwu"
    assert receipt["model"] == "deepseek-v3.2"
    assert receipt["status"] == "completed"
    assert receipt["usage"]["total_tokens"] > 0
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
