from __future__ import annotations

import json
import time

import pytest

from skillrace.campaign_engine import CampaignEngine
from skillrace.resource_pool import ResourcePool

from tests.test_campaign_engine import FakeGenerator, protocol
from tests.test_campaign_engine import SimulatedCrash


class ParallelExecutor:
    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = list(outcomes or [])

    def execute(self, candidate, execution_id, attempt_id, *, lifecycle=None):
        self.calls.append((candidate["candidate_id"], execution_id, attempt_id))
        if lifecycle is not None:
            lifecycle("started", {"run_dir": f"runs/{attempt_id}"})
        time.sleep(0.002 if int(execution_id[1:]) % 2 else 0.01)
        outcome = self.outcomes.pop(0) if self.outcomes else {
            "agent_started": True,
            "status": "completed",
            "runner_status": "completed",
            "oracle_status": "completed",
            "violated": [],
            "inconclusive": [],
            "run_dir": f"runs/{attempt_id}",
        }
        if lifecycle is not None:
            lifecycle("external-terminal", {"result": outcome})
        return outcome


def _engine(tmp_path, generator, executor, *, budget=4, epoch_size=3):
    return CampaignEngine(
        protocol=protocol(budget=budget, bootstrap=0, attempts=3),
        method="random",
        skill="demo",
        out_dir=tmp_path,
        output_identity="parallel-test-output",
        generator=generator,
        executor=executor,
        image_remover=lambda image: None,
        epoch_size=epoch_size,
        resource_pool=ResourcePool(api=2, docker=2, agent=2),
    )


def test_campaign_engine_runs_persisted_epochs_with_stable_ids_and_serial_folds(
    tmp_path,
):
    generator = FakeGenerator("random")
    executor = ParallelExecutor()

    state = _engine(tmp_path, generator, executor).run()

    assert state["complete"] is True
    assert state["counted_executions"] == 4
    assert state["epoch_size"] == 3
    assert state["next_execution_ordinal"] == 4
    assert all(
        record["candidate_id"].startswith("cand-")
        and len(record["candidate_id"]) == 21
        for record in state["iterations"]
    )
    epochs = sorted((tmp_path / "epochs").glob("epoch-*"))
    assert [path.name for path in epochs] == ["epoch-0000", "epoch-0001"]
    for epoch in epochs:
        plan = json.loads((epoch / "plan.json").read_text())
        assert plan["schema"] == "parallel-epoch-plan/1"
        assert all(job["frozen_state_hash"] for job in plan["jobs"])
        assert (epoch / "fold-progress").is_dir()
    first_epoch_ids = [
        item["candidate_id"]
        for item in state["iterations"]
        if item["result"]["epoch"] == 0
    ]
    assert first_epoch_ids == sorted(first_epoch_ids)

    resumed = _engine(tmp_path, FakeGenerator("random"), executor).run()
    assert resumed == state
    assert len(executor.calls) == 4


def test_parallel_campaign_replaces_pre_agent_failure_without_counting_it(tmp_path):
    executor = ParallelExecutor(
        outcomes=[
            {
                "agent_started": False,
                "status": "compile_error",
                "runner_status": "not_started",
                "oracle_status": "not_run",
                "violated": [],
                "inconclusive": [],
            },
            {
                "agent_started": True,
                "status": "completed",
                "runner_status": "completed",
                "oracle_status": "completed",
                "violated": [],
                "inconclusive": [],
                "run_dir": "runs/success-1",
            },
            {
                "agent_started": True,
                "status": "completed",
                "runner_status": "completed",
                "oracle_status": "completed",
                "violated": [],
                "inconclusive": [],
                "run_dir": "runs/success-2",
            },
        ]
    )

    state = _engine(
        tmp_path, FakeGenerator("random"), executor, budget=2, epoch_size=2
    ).run()

    assert state["complete"] is True
    assert state["counted_executions"] == 2
    assert len(state["attempts"]) == 3
    assert sum(item["consume_budget"] for item in state["attempts"]) == 2
    assert state["next_execution_ordinal"] == 3


class EpochOnlyGenerator(FakeGenerator):
    def __init__(self, source):
        super().__init__(source)
        self.epoch_calls = []

    def propose(self):
        raise AssertionError("parallel campaign used scalar propose()")

    def propose_epoch(
        self,
        reservations,
        *,
        batch_dir,
        epoch,
        tree_version,
        frozen_state_hash,
        resource_pool,
    ):
        reservations = list(reservations)
        self.epoch_calls.append(
            {
                "candidate_ids": [item.candidate_id for item in reservations],
                "batch_dir": str(batch_dir),
                "epoch": epoch,
                "tree_version": tree_version,
                "frozen_state_hash": frozen_state_hash,
                "resource_pool": resource_pool,
            }
        )
        self.proposed += len(reservations)
        return [
            {
                "candidate": {
                    "candidate_id": reservation.candidate_id,
                    "provenance": {
                        **dict(reservation.provenance),
                        "source": self.source,
                    },
                },
                "source": self.source,
                "error": None,
            }
            for reservation in reservations
        ]


def test_parallel_campaign_uses_one_frozen_batch_proposal_hook_per_epoch(tmp_path):
    generator = EpochOnlyGenerator("random")
    state = _engine(
        tmp_path,
        generator,
        ParallelExecutor(),
        budget=3,
        epoch_size=3,
    ).run()

    assert state["complete"] is True
    assert len(generator.epoch_calls) == 2
    call = generator.epoch_calls[0]
    assert call["epoch"] == 0
    assert call["tree_version"] == 0
    assert len(call["candidate_ids"]) == 2  # bounded by the shared agent pool
    assert len(set(call["candidate_ids"])) == 2
    assert len(call["frozen_state_hash"]) == 64
    assert call["resource_pool"] is not None
    assert (tmp_path / "epochs" / "epoch-0000" / "generation").is_dir()
    assert len(generator.epoch_calls[1]["candidate_ids"]) == 1


def test_parallel_resume_restores_the_durable_batch_proposal_snapshot(tmp_path):
    artifact = tmp_path / "adaptive.txt"
    artifact.write_text("before")

    class BindingEpochGenerator(FakeGenerator):
        def propose(self):
            raise AssertionError("parallel path used scalar proposal")

        def propose_epoch(self, reservations, **kwargs):
            reservations = list(reservations)
            artifact.write_text("planned")
            self.proposed = len(reservations)
            return [
                {
                    "candidate": {
                        "candidate_id": reservation.candidate_id,
                        "provenance": {
                            **dict(reservation.provenance),
                            "source": self.source,
                        },
                    },
                    "source": self.source,
                    "error": None,
                }
                for reservation in reservations
            ]

        def snapshot(self):
            return {**super().snapshot(), "artifact": artifact.read_text()}

        def restore(self, state):
            if artifact.read_text() != state["artifact"]:
                raise ValueError("adaptive artifact mismatch")
            super().restore(state)

    crashed = False

    def hook(event, context):
        nonlocal crashed
        if event == "after_proposal" and not crashed:
            crashed = True
            raise SimulatedCrash(event)

    with pytest.raises(SimulatedCrash):
        CampaignEngine(
            protocol=protocol(budget=2, bootstrap=0, attempts=3),
            method="random",
            skill="demo",
            out_dir=tmp_path / "campaign",
            output_identity="parallel-binding",
            generator=BindingEpochGenerator("random"),
            executor=ParallelExecutor(),
            fault_hook=hook,
            image_remover=lambda image: None,
            epoch_size=2,
            resource_pool=ResourcePool(api=2, docker=2, agent=2),
        ).run()

    state = CampaignEngine(
        protocol=protocol(budget=2, bootstrap=0, attempts=3),
        method="random",
        skill="demo",
        out_dir=tmp_path / "campaign",
        output_identity="parallel-binding",
        generator=BindingEpochGenerator("random"),
        executor=ParallelExecutor(),
        image_remover=lambda image: None,
        epoch_size=2,
        resource_pool=ResourcePool(api=2, docker=2, agent=2),
    ).run()

    assert state["complete"] is True
    assert state["counted_executions"] == 2
