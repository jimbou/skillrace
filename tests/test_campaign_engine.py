from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

import pytest

from skillrace.campaign_engine import CampaignEngine
from skillrace.campaign_protocol import CampaignProtocol
from skillrace.closeai import OutcomeUnknownError
from skillrace.loop import (
    RealCampaignExecutor,
    campaign_output_identity,
    resolve_base_image_identity,
)


def protocol(*, budget=4, bootstrap=2, attempts=3):
    return CampaignProtocol.from_dict(
        {
            "schema": "campaign-protocol/1",
            "protocol_id": "engine-test-v1",
            "status": "runtime",
            "model": "glm-4.5-flash",
            "budget": budget,
            "bootstrap_count": bootstrap,
            "max_generation_attempts_per_execution": attempts,
            "seed_generator": {
                "batch_size": 1,
                "temperature": 0.9,
                "build_retries": 4,
            },
            "greybox_level": "L1",
            "random_seed": 7,
            "repair": {
                "enabled": True,
                "timeout_seconds": 120,
                "max_output_tokens": 4000,
                "temperature": 0.0,
                "reasoning": True,
                "backend_by_method": {
                    "random": "direct",
                    "greybox": "direct",
                    "skillrace": "pi",
                },
            },
        }
    )


@dataclass
class FakeGenerator:
    source: str
    proposed: int = 0
    folds: list = field(default_factory=list)
    folded_attempt_ids: list = field(default_factory=list)

    def propose(self):
        candidate = {
            "candidate_id": f"{self.source}-{self.proposed}",
            "provenance": {"source": self.source},
        }
        self.proposed += 1
        return candidate

    def fold(self, candidate, run_dir, phase="explore", attempt_id=None):
        if attempt_id in self.folded_attempt_ids:
            return {"duplicate": True}
        self.folds.append((candidate["candidate_id"], phase, str(run_dir)))
        if attempt_id is not None:
            self.folded_attempt_ids.append(attempt_id)
        return {"candidate_id": candidate["candidate_id"], "phase": phase}

    def snapshot(self):
        return {
            "source": self.source,
            "proposed": self.proposed,
            "folds": list(self.folds),
            "folded_attempt_ids": list(self.folded_attempt_ids),
        }

    def restore(self, state):
        if state["source"] != self.source:
            raise ValueError("generator source mismatch")
        self.proposed = state["proposed"]
        self.folds = [tuple(item) for item in state["folds"]]
        self.folded_attempt_ids = list(state["folded_attempt_ids"])


class FakeExecutor:
    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = list(outcomes or [])

    def execute(self, candidate, execution_id, attempt_id):
        self.calls.append((candidate["candidate_id"], execution_id, attempt_id))
        result = self.outcomes.pop(0) if self.outcomes else {
            "agent_started": True,
            "status": "completed",
            "oracle_status": "completed",
            "violated": [],
            "inconclusive": [],
        }
        return {
            "run_dir": f"runs/{attempt_id}",
            "candidate": candidate,
            **result,
        }


def engine(tmp_path, *, method="random", generator=None, bootstrap=None,
           executor=None, campaign_protocol=None, hook=None, output_identity=None):
    return CampaignEngine(
        protocol=campaign_protocol or protocol(),
        method=method,
        skill="demo-skill",
        out_dir=tmp_path,
        output_identity=output_identity,
        generator=generator or FakeGenerator(method),
        bootstrap_generator=bootstrap,
        executor=executor or FakeExecutor(),
        fault_hook=hook,
        image_remover=lambda image: None,
    )


def test_schema_and_equal_counted_30_run_budgets(tmp_path):
    for method in ("random", "greybox", "skillrace"):
        out = tmp_path / method
        main = FakeGenerator(method)
        bootstrap = FakeGenerator(f"{method}-bootstrap")
        state = engine(
            out,
            method=method,
            generator=main,
            bootstrap=bootstrap,
            campaign_protocol=protocol(budget=30, bootstrap=10),
        ).run()

        assert state["schema"] == "campaign/2"
        assert state["counted_executions"] == 30
        assert len(state["iterations"]) == 30
        assert state["complete"] is True
        assert [item["execution_id"] for item in state["iterations"]] == [
            f"e{i:04d}" for i in range(30)
        ]
        if method == "random":
            assert bootstrap.proposed == 0
            assert main.proposed == 30
            assert {phase for _, phase, _ in main.folds} == {"explore"}
        else:
            assert bootstrap.proposed == 10
            assert main.proposed == 20
            assert [phase for _, phase, _ in main.folds[:10]] == ["bootstrap"] * 10


def test_real_output_identity_covers_path_base_properties_and_applicability(tmp_path):
    arguments = {
        "out_dir": tmp_path / "campaign",
        "base_image": "demo:base",
        "base_image_identity": "sha256:" + "a" * 64,
        "skill_input_hash": "b" * 64,
        "properties": [{"id": "p1", "nl": "works"}],
        "applicability": {"property_ids": ["p1"]},
    }
    identity = campaign_output_identity(**arguments)
    assert identity == campaign_output_identity(**arguments)
    for field, changed in (
        ("out_dir", tmp_path / "other"),
        ("base_image", "demo:other"),
        ("base_image_identity", "sha256:" + "c" * 64),
        ("skill_input_hash", "d" * 64),
        ("properties", [{"id": "p2", "nl": "works"}]),
        ("applicability", {"property_ids": []}),
    ):
        assert campaign_output_identity(**{**arguments, field: changed}) != identity


def test_base_image_identity_is_resolved_without_executing_the_image():
    calls = []
    identity = resolve_base_image_identity(
        "demo:base",
        resolver=lambda image: calls.append(image) or ("sha256:" + "a" * 64),
    )
    assert identity == "sha256:" + "a" * 64
    assert calls == ["demo:base"]


def test_base_image_identity_rejects_a_mutable_or_malformed_answer():
    with pytest.raises(ValueError, match="immutable base-image"):
        resolve_base_image_identity("demo:base", resolver=lambda image: "demo:base")


def test_pre_agent_failures_use_new_candidate_attempt_without_counting(tmp_path):
    executor = FakeExecutor(
        [
            {"agent_started": False, "status": "sanity_rejected", "oracle_status": "not_run"},
            {"agent_started": False, "status": "runtime_infrastructure_error", "oracle_status": "not_run"},
            {"agent_started": True, "status": "timeout", "oracle_status": "error"},
        ]
    )
    state = engine(
        tmp_path,
        executor=executor,
        campaign_protocol=protocol(budget=1, bootstrap=0, attempts=3),
    ).run()

    assert [item["attempt_id"] for item in state["attempts"]] == [
        "e0000-a00", "e0000-a01", "e0000-a02"
    ]
    assert [item["consume_budget"] for item in state["attempts"]] == [False, False, True]
    assert state["iterations"][0]["runner_status"] == "timeout"
    assert state["iterations"][0]["oracle_status"] == "error"


def test_attempt_cap_is_finite_and_records_stop_reason(tmp_path):
    executor = FakeExecutor(
        [{"agent_started": False, "status": "compile_error"}] * 2
    )
    state = engine(
        tmp_path,
        executor=executor,
        campaign_protocol=protocol(budget=1, bootstrap=0, attempts=2),
    ).run()

    assert state["counted_executions"] == 0
    assert state["complete"] is False
    assert state["stop_reason"] == "generation-attempt-cap"
    assert len(executor.calls) == 2


def test_unknown_generation_call_stops_campaign_without_retry(tmp_path):
    class UnknownGenerator(FakeGenerator):
        def propose(self):
            self.proposed += 1
            raise OutcomeUnknownError("operation outcome is unknown")

    generator = UnknownGenerator("random")
    executor = FakeExecutor()
    state = engine(
        tmp_path,
        generator=generator,
        executor=executor,
        campaign_protocol=protocol(budget=1, bootstrap=0, attempts=3),
    ).run()

    assert generator.proposed == 1
    assert executor.calls == []
    assert len(state["attempts"]) == 1
    assert state["counted_executions"] == 0
    assert state["status"] == "aborted_external_outcome_unknown"
    assert state["stop_reason"] == "external-outcome-unknown"
    result = state["attempts"][0]["result"]
    assert result["status"] == "external-outcome-indeterminate"
    assert result["cost_accounting"] == "unknown-nonzero-possible"


def test_invalid_candidate_identity_is_a_pre_agent_generation_failure(tmp_path):
    class UnsafeGenerator(FakeGenerator):
        def propose(self):
            self.proposed += 1
            return {"candidate_id": "../escape", "provenance": {"source": "random"}}

    class ForbiddenExecutor:
        def execute(self, *args):
            raise AssertionError("unsafe candidate reached the executor")

    state = engine(
        tmp_path,
        generator=UnsafeGenerator("random"),
        executor=ForbiddenExecutor(),
        campaign_protocol=protocol(budget=1, bootstrap=0, attempts=1),
    ).run()

    assert state["iterations"] == []
    assert state["attempts"][0]["generation_status"] == "generation_error"
    assert "candidate_id" in state["attempts"][0]["error"]


class SimulatedCrash(RuntimeError):
    pass


@pytest.mark.parametrize(
    "boundary",
    [
        "after_receipt",
        "after_commit",
        "after_fold",
        "after_fold_receipt",
        "after_finalize",
    ],
)
def test_resume_at_every_durable_boundary_never_reexecutes_or_refolds(
    tmp_path, boundary
):
    calls = []

    class Executor(FakeExecutor):
        def execute(self, candidate, execution_id, attempt_id):
            calls.append(attempt_id)
            return super().execute(candidate, execution_id, attempt_id)

    crashed = False

    def hook(event, context):
        nonlocal crashed
        if not crashed and event == boundary and context["execution_id"] == "e0001":
            crashed = True
            raise SimulatedCrash(boundary)

    first_generator = FakeGenerator("random")
    with pytest.raises(SimulatedCrash):
        engine(
            tmp_path,
            generator=first_generator,
            executor=Executor(),
            campaign_protocol=protocol(budget=4, bootstrap=0),
            hook=hook,
        ).run()

    resumed_generator = FakeGenerator("random")
    state = engine(
        tmp_path,
        generator=resumed_generator,
        executor=Executor(),
        campaign_protocol=protocol(budget=4, bootstrap=0),
    ).run()

    assert state["complete"] is True
    assert state["counted_executions"] == 4
    assert calls == ["e0000-a00", "e0001-a00", "e0002-a00", "e0003-a00"]
    assert [item[0] for item in resumed_generator.folds] == [
        "random-0", "random-1", "random-2", "random-3"
    ]
    assert len(resumed_generator.folded_attempt_ids) == 4


def test_resume_recovers_executor_terminal_journal_before_receipt(tmp_path):
    calls = []

    class Executor(FakeExecutor):
        def execute(self, candidate, execution_id, attempt_id):
            calls.append(attempt_id)
            return super().execute(candidate, execution_id, attempt_id)

    crashed = False

    def hook(event, context):
        nonlocal crashed
        if event == "after_executor_terminal" and not crashed:
            crashed = True
            raise SimulatedCrash(event)

    with pytest.raises(SimulatedCrash):
        engine(
            tmp_path,
            generator=FakeGenerator("random"),
            executor=Executor(),
            campaign_protocol=protocol(budget=1, bootstrap=0),
            hook=hook,
        ).run()

    state = engine(
        tmp_path,
        generator=FakeGenerator("random"),
        executor=Executor(),
        campaign_protocol=protocol(budget=1, bootstrap=0),
    ).run()

    assert state["complete"] is True
    assert calls == ["e0000-a00"]
    assert state["iterations"][0]["status"] == "completed"


def test_resume_marks_started_unknown_counted_and_never_reruns_executor(tmp_path):
    calls = []

    class CrashingExecutor:
        def execute(self, candidate, execution_id, attempt_id, *, lifecycle):
            calls.append(attempt_id)
            lifecycle("started", {"run_dir": f"runs/{attempt_id}"})
            raise SystemExit("process died while the external agent was running")

    with pytest.raises(SystemExit):
        engine(
            tmp_path,
            generator=FakeGenerator("random"),
            executor=CrashingExecutor(),
            campaign_protocol=protocol(budget=1, bootstrap=0),
        ).run()

    class ForbiddenExecutor:
        def execute(self, *args, **kwargs):
            raise AssertionError("started external action was silently rerun")

    state = engine(
        tmp_path,
        generator=FakeGenerator("random"),
        executor=ForbiddenExecutor(),
        campaign_protocol=protocol(budget=1, bootstrap=0),
    ).run()

    result = state["iterations"][0]["result"]
    assert calls == ["e0000-a00"]
    assert state["counted_executions"] == 1
    assert result["agent_started"] is None
    assert result["launch_committed"] is None
    assert result["status"] == "external-outcome-indeterminate"
    assert result["lifecycle_recovery"] == "started-only"
    assert result["cost_accounting"] == "unknown-nonzero-possible"
    assert result["consume_budget_conservatively"] is True
    assert result["budget_accounting_reason"] == "launch-state-indeterminate"


def test_resume_recovers_external_terminal_result_without_rerunning_agent(tmp_path):
    calls = []

    class CrashingExecutor:
        def execute(self, candidate, execution_id, attempt_id, *, lifecycle):
            calls.append(attempt_id)
            lifecycle("started", {"run_dir": f"runs/{attempt_id}"})
            lifecycle(
                "external-terminal",
                {
                    "result": {
                        "agent_started": True,
                        "status": "completed",
                        "runner_status": "completed",
                        "infrastructure_status": "ready",
                        "oracle_status": "not_run",
                        "violated": [],
                        "inconclusive": [],
                        "run_dir": f"runs/{attempt_id}",
                    }
                },
            )
            raise SystemExit("process died after the external action")

    with pytest.raises(SystemExit):
        engine(
            tmp_path,
            generator=FakeGenerator("random"),
            executor=CrashingExecutor(),
            campaign_protocol=protocol(budget=1, bootstrap=0),
        ).run()

    class ForbiddenExecutor:
        def execute(self, *args, **kwargs):
            raise AssertionError("terminal external action was silently rerun")

    state = engine(
        tmp_path,
        generator=FakeGenerator("random"),
        executor=ForbiddenExecutor(),
        campaign_protocol=protocol(budget=1, bootstrap=0),
    ).run()

    result = state["iterations"][0]["result"]
    assert calls == ["e0000-a00"]
    assert result["status"] == "completed"
    assert result["lifecycle_recovery"] == "external-terminal"
    assert result["cost_accounting"] == "unknown-nonzero-possible"
    assert result["unrecorded_cost_possible"] is True


def test_resume_refuses_protocol_method_skill_and_output_identity_mismatch(tmp_path):
    state = engine(
        tmp_path,
        campaign_protocol=protocol(budget=1, bootstrap=0),
        output_identity="reviewed-output-a",
    ).run()
    assert state["complete"]

    changed = protocol(budget=2, bootstrap=0)
    with pytest.raises(ValueError, match="protocol hash"):
        engine(tmp_path, campaign_protocol=changed, output_identity="reviewed-output-a").run()
    with pytest.raises(ValueError, match="method"):
        CampaignEngine(
            protocol=protocol(budget=1, bootstrap=0), method="greybox",
            skill="demo-skill", out_dir=tmp_path, output_identity="reviewed-output-a",
            generator=FakeGenerator("greybox"), bootstrap_generator=FakeGenerator("boot"),
            executor=FakeExecutor(), image_remover=lambda image: None,
        ).run()
    with pytest.raises(ValueError, match="skill"):
        CampaignEngine(
            protocol=protocol(budget=1, bootstrap=0), method="random",
            skill="different", out_dir=tmp_path, output_identity="reviewed-output-a",
            generator=FakeGenerator("random"), executor=FakeExecutor(),
            image_remover=lambda image: None,
        ).run()
    with pytest.raises(ValueError, match="output identity"):
        engine(
            tmp_path,
            campaign_protocol=protocol(budget=1, bootstrap=0),
            output_identity="reviewed-output-b",
        ).run()


def test_resume_rejects_embedded_protocol_tampering_even_when_hash_field_is_unchanged(
    tmp_path,
):
    state = engine(
        tmp_path, campaign_protocol=protocol(budget=1, bootstrap=0)
    ).run()
    state["protocol"]["budget"] = 99
    (tmp_path / "campaign.json").write_text(json.dumps(state))

    with pytest.raises(ValueError, match="embedded protocol"):
        engine(tmp_path, campaign_protocol=protocol(budget=1, bootstrap=0)).run()


def test_deterministic_execution_and_attempt_id_ranges_are_bounded(tmp_path):
    with pytest.raises(ValueError, match="execution ID"):
        engine(
            tmp_path / "budget",
            campaign_protocol=protocol(budget=10_001, bootstrap=0),
        )
    with pytest.raises(ValueError, match="attempt ID"):
        engine(
            tmp_path / "attempts",
            campaign_protocol=protocol(budget=1, bootstrap=0, attempts=101),
        )


def test_malformed_or_conflicting_immutable_receipt_is_rejected(tmp_path):
    crashed = False

    def hook(event, context):
        nonlocal crashed
        if event == "after_receipt" and not crashed:
            crashed = True
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        engine(
            tmp_path,
            campaign_protocol=protocol(budget=1, bootstrap=0),
            hook=hook,
        ).run()

    receipt = tmp_path / "attempts" / "e0000-a00" / "receipt.json"
    receipt.write_text("not-json")
    with pytest.raises(ValueError, match="malformed.*receipt"):
        engine(tmp_path, campaign_protocol=protocol(budget=1, bootstrap=0)).run()

    # A well-formed but changed receipt conflicts with the immutable hash in state.
    other = tmp_path / "other"
    state = engine(other, campaign_protocol=protocol(budget=1, bootstrap=0)).run()
    assert state["complete"]
    receipt = other / "attempts" / "e0000-a00" / "receipt.json"
    data = json.loads(receipt.read_text())
    data["result"]["status"] = "timeout"
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="conflicting.*receipt"):
        engine(other, campaign_protocol=protocol(budget=1, bootstrap=0)).run()


def test_committed_proposal_is_also_immutable_and_audited(tmp_path):
    state = engine(
        tmp_path, campaign_protocol=protocol(budget=1, bootstrap=0)
    ).run()
    assert state["complete"]
    proposal = tmp_path / "attempts" / "e0000-a00" / "proposal.json"
    data = json.loads(proposal.read_text())
    data["candidate"]["candidate_id"] = "changed-after-review"
    proposal.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="conflicting.*proposal"):
        engine(tmp_path, campaign_protocol=protocol(budget=1, bootstrap=0)).run()


def test_fold_receipt_preserves_oracle_inconclusive_and_classification(tmp_path):
    executor = FakeExecutor(
        [{
            "agent_started": True,
            "status": "completed",
            "oracle_status": "inconclusive",
            "inconclusive": ["p1"],
            "classification": "serendipitous",
        }]
    )
    state = engine(
        tmp_path,
        executor=executor,
        campaign_protocol=protocol(budget=1, bootstrap=0),
    ).run()

    iteration = state["iterations"][0]
    assert iteration["oracle_status"] == "inconclusive"
    assert iteration["inconclusive"] == ["p1"]
    assert iteration["classification"] == "serendipitous"
    fold = json.loads(
        (tmp_path / "attempts" / "e0000-a00" / "fold.json").read_text()
    )
    assert fold["classification"] == "serendipitous"


def test_real_executor_uses_one_ordered_trusted_pipeline(tmp_path, monkeypatch):
    import skillrace.loop as loop_module

    events = []
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    candidate = {
        "candidate_id": "c1",
        "skill": "demo",
        "prompt": "fix it",
        "base_image": "demo:base",
        "built_image": "skillrace/c1:built",
        "containerfile": "FROM demo:base\n",
        "sanity": {},
        "provenance": {"source": "random"},
    }
    monkeypatch.setattr(
        loop_module,
        "verify_runtime_integrity",
        lambda *args: events.append("runtime") or {"trusted": True},
    )
    monkeypatch.setattr(
        loop_module,
        "run_candidate_sanity",
        lambda *args: events.append("sanity") or {
            "schema": "candidate-sanity/1", "valid": True, "checks": []
        },
    )
    assert not hasattr(loop_module, "compile_case")

    def run_agent(case_dir, run_dir, *args):
        events.append("agent")
        run_dir.mkdir(parents=True)
        (run_dir / "cost.json").write_text(
            json.dumps({"turns": 2, "in": 10, "out": 5, "price_provider_credits": 0.02})
        )
        manifest = {
            "run_id": "agent-real-001",
            "agent_started": True,
            "termination": {"reason": "completed"},
        }
        (run_dir / "run.json").write_text(json.dumps(manifest))
        return 0, "ok", manifest

    monkeypatch.setattr(loop_module, "run_agent", run_agent)
    checker_inputs = []

    def check_run(*args, **kwargs):
        events.append("checker")
        checker_inputs.append(kwargs)
        return ([{"property_id": "p1", "holds": None, "violated": False}], [], 0)

    monkeypatch.setattr(loop_module, "check_run", check_run)
    executor = RealCampaignExecutor(
        skill="demo", skill_dir=skill_dir, cases_dir=tmp_path / "cases",
        runs_dir=tmp_path / "runs", properties=[{"id": "p1"}],
        applicability={"property_ids": ["p1"]}, model="glm-4.5-flash",
        wall_clock=5,
    )

    result = executor.execute(candidate, "e0000", "e0000-a00")

    assert events == ["runtime", "sanity", "agent", "checker"]
    assert checker_inputs == [
        {
            "properties": [{"id": "p1"}],
            "candidate": candidate,
            "applicability": {"property_ids": ["p1"]},
        }
    ]
    assert result["agent_started"] is True
    assert result["run_id"] == "agent-real-001"
    assert result["oracle_status"] == "inconclusive"
    assert result["inconclusive"] == ["p1"]
    assert result["run_cost_receipt"]["cost"]["price_provider_credits"] == 0.02
    assert len(result["run_cost_receipt"]["cost_hash"]) == 64
    assert pathlib.Path(result["case_dir"]).is_dir()
    assert pathlib.Path(result["run_dir"]).name.startswith("e0000-a00-")


def test_real_executor_rejection_never_calls_later_consumers(tmp_path, monkeypatch):
    import skillrace.loop as loop_module

    candidate = {
        "candidate_id": "c1", "base_image": "demo:base",
        "built_image": "skillrace/c1:built", "containerfile": "FROM demo:base\n",
        "sanity": {}, "provenance": {"source": "random"},
    }
    monkeypatch.setattr(loop_module, "verify_runtime_integrity", lambda *a: {})
    monkeypatch.setattr(
        loop_module,
        "run_candidate_sanity",
        lambda *a: {"schema": "candidate-sanity/1", "valid": False,
                    "rejection": "unsolved", "checks": []},
    )
    monkeypatch.setattr(
        loop_module,
        "check_run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("checker called")),
    )
    monkeypatch.setattr(
        loop_module,
        "run_agent",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("agent called")),
    )
    executor = RealCampaignExecutor(
        skill="demo", skill_dir=tmp_path, cases_dir=tmp_path / "cases",
        runs_dir=tmp_path / "runs", properties=[], applicability={},
        model="glm-4.5-flash", wall_clock=5,
    )

    result = executor.execute(candidate, "e0000", "e0000-a00")

    assert result["agent_started"] is False
    assert result["status"] == "sanity_rejected"
    assert result["oracle_status"] == "not_run"


def test_skillrace_fold_uses_persisted_target_and_observed_violation_relationships(
    tmp_path,
):
    from skillrace.loop import _SkillRACEEngineAdapter

    class Inner:
        skill = "demo"
        last_target_parent = "wrong-ambient-target"

        def fold(self, candidate, run_dir, phase="explore", attempt_id=None):
            return [("merge", "n0", ""), ("new", "n8", "")]

    adapter = _SkillRACEEngineAdapter(Inner(), tmp_path)
    candidate = {
        "candidate_id": "cand-1",
        "provenance": {
            "source": "skillrace",
            "target_parent": "n4",
            "targeted_property": "p-target",
            "validation": {"validated": True, "validate_sh": "true"},
        },
        "_execution_result": {
            "violated": ["p-other", "p-target"],
            "oracle_status": "completed",
        },
    }

    folded = adapter.fold(
        candidate, tmp_path / "run", phase="explore", attempt_id="e0000-a00"
    )

    assert folded["classification"] == "different_new_branch"
    assert folded["observed_violation_count"] == 2
    assert folded["confirmation_status"] == "unconfirmed"
    assert folded["discoveries"] == [
        {"property_id": "p-other", "relationship": "serendipitous"},
        {"property_id": "p-target", "relationship": "targeted"},
    ]


def test_engine_recovers_proven_skillrace_forward_fold_without_reexecuting_agent(
    tmp_path,
):
    from skillrace.loop import SkillRACEGenerator

    class Seed:
        cost_provider_credits = 0.0

        def snapshot(self):
            return {"schema": "seed/1"}

        def restore(self, snapshot):
            assert snapshot == {"schema": "seed/1"}

    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("trusted")
    output = tmp_path / "campaign"
    output.mkdir()

    class ForwardGenerator:
        skill = "demo-skill"

        def __init__(self):
            self.inner = SkillRACEGenerator(
                "demo-skill", skill, "demo:base", [{"id": "p1"}], "model",
                output, Seed(), base_image_identity="sha256:" + "a" * 64,
            )
            if not self.inner.tree_path.exists():
                self.inner.tree_path.write_text(
                    json.dumps({"schema": "behavior-tree/2", "folded_attempts": {}})
                )

        def propose(self):
            return {"candidate_id": "c1", "provenance": {"source": "skillrace"}}

        def snapshot(self):
            return self.inner.snapshot()

        def restore(self, snapshot):
            self.inner.restore(snapshot)

        def restore_for_pending_fold(self, snapshot, attempt_id):
            return self.inner.restore_for_pending_fold(snapshot, attempt_id)

        def fold(self, candidate, run_dir, phase="explore", attempt_id=None):
            tree = json.loads(self.inner.tree_path.read_text())
            prior = tree.get("folded_attempts", {}).get(attempt_id)
            if prior is None:
                prior = {"actions": [["new", "n0", "x"]]}
                tree.setdefault("folded_attempts", {})[attempt_id] = prior
                self.inner.tree_path.write_text(json.dumps(tree))
                self.inner.tree_path.with_suffix(".cache.json").write_text(
                    json.dumps({"fold": attempt_id})
                )
                self.inner.publish_fold_artifact_version(attempt_id)
            if attempt_id not in self.inner.folded_attempt_ids:
                self.inner.folded_attempt_ids.append(attempt_id)
                self.inner._fold_results[attempt_id] = prior["actions"]
            return prior["actions"]

    calls = []

    class Executor(FakeExecutor):
        def execute(self, candidate, execution_id, attempt_id):
            calls.append(attempt_id)
            return super().execute(candidate, execution_id, attempt_id)

    crashed = False

    def hook(event, context):
        nonlocal crashed
        if event == "after_fold" and not crashed:
            crashed = True
            raise SimulatedCrash(event)

    with pytest.raises(SimulatedCrash):
        CampaignEngine(
            protocol=protocol(budget=1, bootstrap=0), method="skillrace",
            skill="demo-skill", out_dir=output, generator=ForwardGenerator(),
            executor=Executor(), image_remover=lambda image: None, fault_hook=hook,
        ).run()

    final_generator = ForwardGenerator()
    state = CampaignEngine(
        protocol=protocol(budget=1, bootstrap=0), method="skillrace",
        skill="demo-skill", out_dir=output, generator=final_generator,
        executor=Executor(), image_remover=lambda image: None,
    ).run()
    assert state["complete"] is True
    assert calls == ["e0000-a00"]
    assert final_generator.inner.folded_attempt_ids == ["e0000-a00"]


def test_resume_uses_durable_uncommitted_proposal_snapshot_for_artifact_state(tmp_path):
    artifact = tmp_path / "adaptive.json"
    artifact.write_text("0")

    class BindingGenerator:
        source = "random"

        def propose(self):
            artifact.write_text("1")
            return {"candidate_id": "c1"}

        def snapshot(self):
            return {"source": self.source, "artifact": artifact.read_text()}

        def restore(self, snapshot):
            if artifact.read_text() != snapshot["artifact"]:
                raise ValueError("adaptive artifact mismatch")

        def fold(self, candidate, run_dir, phase="explore", attempt_id=None):
            return None

    crashed = False

    def hook(event, context):
        nonlocal crashed
        if event == "after_proposal" and not crashed:
            crashed = True
            raise SimulatedCrash(event)

    calls = []

    class Executor(FakeExecutor):
        def execute(self, candidate, execution_id, attempt_id):
            calls.append(attempt_id)
            return super().execute(candidate, execution_id, attempt_id)

    with pytest.raises(SimulatedCrash):
        CampaignEngine(
            protocol=protocol(budget=1, bootstrap=0), method="random", skill="demo",
            out_dir=tmp_path / "out", generator=BindingGenerator(), executor=Executor(),
            image_remover=lambda image: None, fault_hook=hook,
        ).run()

    state = CampaignEngine(
        protocol=protocol(budget=1, bootstrap=0), method="random", skill="demo",
        out_dir=tmp_path / "out", generator=BindingGenerator(), executor=Executor(),
        image_remover=lambda image: None,
    ).run()
    assert state["complete"] is True
    assert calls == ["e0000-a00"]
