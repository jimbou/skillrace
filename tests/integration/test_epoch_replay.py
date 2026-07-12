from __future__ import annotations

import json
import threading
import time

from skillrace.campaign_engine import CampaignEngine
from skillrace.io_utils import atomic_write_json, canonical_json_hash
from skillrace.loop import summarize_skillrace_discoveries
from skillrace.resource_pool import ResourcePool

from tests.test_campaign_engine import protocol


class ReplayAdaptiveGenerator:
    """Small adaptive generator that exposes every reducer-owned artifact."""

    def __init__(self, artifact_dir):
        self.artifact_dir = artifact_dir
        self.proposed = 0
        self.folded_attempt_ids = []
        self.fold_results = {}
        self.tree = {"schema": "replay-tree/1", "nodes": []}
        self.cache = {"schema": "replay-cache/1", "decisions": {}}
        self.guards = {"schema": "replay-guards/1", "tried": {}}
        self.classifications = []
        self._write_artifacts()

    def _write_artifacts(self):
        atomic_write_json(self.artifact_dir / "tree.json", self.tree)
        atomic_write_json(self.artifact_dir / "tree.cache.json", self.cache)
        atomic_write_json(self.artifact_dir / "tree.guards.json", self.guards)
        atomic_write_json(
            self.artifact_dir / "classifications.json", self.classifications
        )

    def propose_epoch(self, reservations, *, frozen_state_hash, **kwargs):
        reservations = list(reservations)
        self.proposed += len(reservations)
        return [
            {
                "candidate": {
                    "candidate_id": reservation.candidate_id,
                    "skill": "demo",
                    "prompt": "exercise a frozen target",
                    "provenance": {
                        **dict(reservation.provenance),
                        "source": "skillrace",
                        "branch_key": f"branch-{reservation.slot}",
                        "mutation": f"mutation-{reservation.slot}",
                        "target_parent": f"parent-{reservation.slot}",
                        "targeted_property": "p-target",
                        "frozen_state_hash": frozen_state_hash,
                        "validation": {
                            "validated": True,
                            "validate_sh": "true",
                            "target_condition": f"mutation-{reservation.slot}",
                        },
                    },
                },
                "source": "skillrace",
                "error": None,
            }
            for reservation in reservations
        ]

    def fold(self, candidate, run_dir, phase="explore", attempt_id=None):
        if attempt_id in self.fold_results:
            return self.fold_results[attempt_id]
        result = candidate.pop("_execution_result")
        provenance = candidate["provenance"]
        slot = int(provenance["slot"])
        parent = provenance["target_parent"]
        if slot % 3 == 0:
            actions = [("match", parent, {}), ("new", f"child-{slot}", {})]
        elif slot % 3 == 1:
            actions = [("new", f"other-{slot}", {})]
        else:
            actions = [("match", parent, {}), ("match", f"old-{slot}", {})]
        summary = summarize_skillrace_discoveries(
            actions,
            target_parent=parent,
            violated_property_ids=result["violated"],
            targeted_property=provenance["targeted_property"],
        )
        logical = {
            "attempt_id": attempt_id,
            "candidate_id": candidate["candidate_id"],
            "run_dir": str(run_dir),
            "classification": summary["branch_outcome"],
            "discoveries": summary["discoveries"],
        }
        self.tree["nodes"].append(logical)
        self.cache["decisions"][candidate["candidate_id"]] = {
            "actions_hash": canonical_json_hash(actions),
            "classification": summary["branch_outcome"],
        }
        self.guards["tried"].setdefault(provenance["branch_key"], []).append(
            provenance["mutation"]
        )
        self.classifications.append(logical)
        self.folded_attempt_ids.append(attempt_id)
        fold_result = {**summary, "classification": summary["branch_outcome"]}
        self.fold_results[attempt_id] = fold_result
        self._write_artifacts()
        return fold_result

    def snapshot(self):
        return {
            "schema": "replay-adaptive-generator/1",
            "proposed": self.proposed,
            "folded_attempt_ids": list(self.folded_attempt_ids),
            "fold_results": json.loads(json.dumps(self.fold_results)),
            "tree": json.loads(json.dumps(self.tree)),
            "cache": json.loads(json.dumps(self.cache)),
            "guards": json.loads(json.dumps(self.guards)),
            "classifications": json.loads(json.dumps(self.classifications)),
        }

    def restore(self, state):
        if state.get("schema") != "replay-adaptive-generator/1":
            raise ValueError("unexpected replay generator state")
        self.proposed = state["proposed"]
        self.folded_attempt_ids = list(state["folded_attempt_ids"])
        self.fold_results = json.loads(json.dumps(state["fold_results"]))
        self.tree = json.loads(json.dumps(state["tree"]))
        self.cache = json.loads(json.dumps(state["cache"]))
        self.guards = json.loads(json.dumps(state["guards"]))
        self.classifications = json.loads(json.dumps(state["classifications"]))
        self._write_artifacts()


class OrderedCompletionExecutor:
    def __init__(self, direction, workers=4):
        self.direction = direction
        self.barrier = threading.Barrier(workers)
        self.completion_order = []
        self.lock = threading.Lock()

    def execute(self, candidate, execution_id, attempt_id, *, lifecycle=None):
        slot = int(candidate["provenance"]["slot"])
        run_dir = f"runs/{attempt_id}"
        if lifecycle is not None:
            lifecycle(
                "started",
                {
                    "run_dir": run_dir,
                    "launch_committed": True,
                    "agent_started": True,
                },
            )
        self.barrier.wait(timeout=2)
        rank = slot if self.direction == "ascending" else 3 - slot
        time.sleep(0.01 * (rank + 1))
        with self.lock:
            self.completion_order.append(candidate["candidate_id"])
        violated = ["p-target"] if slot % 2 == 0 else ["p-other"]
        outcome = {
            "agent_started": True,
            "status": "completed",
            "runner_status": "completed",
            "oracle_status": "completed",
            "violated": violated,
            "inconclusive": [],
            "run_dir": run_dir,
        }
        if lifecycle is not None:
            lifecycle("external-terminal", {"result": outcome})
        return outcome


def _run(root, direction):
    generator = ReplayAdaptiveGenerator(root)
    executor = OrderedCompletionExecutor(direction)
    state = CampaignEngine(
        protocol=protocol(budget=4, bootstrap=0, attempts=3),
        method="skillrace",
        skill="demo",
        out_dir=root,
        output_identity="completion-order-replay-v1",
        generator=generator,
        executor=executor,
        image_remover=lambda image: None,
        epoch_size=4,
        resource_pool=ResourcePool(api=4, docker=4, agent=4),
    ).run()
    return state, executor.completion_order


def test_epoch_completion_order_does_not_change_any_logical_adaptive_artifact(
    tmp_path,
):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first, first_completion = _run(first_root, "ascending")
    second, second_completion = _run(second_root, "descending")

    assert first_completion == list(reversed(second_completion))
    assert first_completion != second_completion
    for name in (
        "tree.json",
        "tree.cache.json",
        "tree.guards.json",
        "classifications.json",
        "campaign.json",
    ):
        assert (first_root / name).read_bytes() == (second_root / name).read_bytes()
    assert canonical_json_hash(first["generator_state"]) == canonical_json_hash(
        second["generator_state"]
    )
    assert [item["classification"] for item in first["iterations"]] == [
        item["classification"] for item in second["iterations"]
    ]
