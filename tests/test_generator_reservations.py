from __future__ import annotations

import json

import pytest

import skillrace.generator as generator_module
import skillrace.greybox as greybox_module
import skillrace.guards as guards_module
import skillrace.loop as loop_module
from skillrace.parallel_campaign import make_reservations


SANITY = {
    "required_paths": ["/workspace"],
    "required_tools": ["bash"],
    "task_probe": {"command": "true", "allowed_exit_codes": [0]},
    "unsolved_check": None,
}


def _artifact(candidate_id, base="demo:base"):
    return {
        "prompt": "do it",
        "containerfile": f"FROM {base}\n",
        "built_image": f"skillrace/{candidate_id}:built",
        "sanity": SANITY,
        "build_attempts": 1,
    }


def test_random_reserves_stable_identities_before_independent_realization(
    tmp_path, monkeypatch
):
    generator = generator_module.RandomGenerator(
        "demo", "/definitely/missing", "demo:base"
    )
    reservations = make_reservations(
        "protocol/rep/random/demo",
        [("e0000", "e0000-a00"), ("e0001", "e0001-a00")],
        epoch=3,
    )
    items = [
        {"summary": "one", "task": "task one", "env": "env one"},
        {"summary": "two", "task": "task two", "env": "env two"},
    ]
    before = generator.snapshot()
    batch_path = tmp_path / "random-batch.json"
    batch = generator.reserve_batch(
        items,
        reservations,
        batch_path=batch_path,
        proposal_cost_provider_credits=0.3,
    )

    assert [item["candidate_id"] for item in batch] == [
        reservation.candidate_id for reservation in reservations
    ]
    assert batch[0]["provenance"]["epoch"] == 3
    assert batch[0]["item"] == items[0]
    assert batch_path.is_file()
    assert generator.n_batches == 1
    assert generator.cost_provider_credits == 0.3

    replay = generator_module.RandomGenerator(
        "demo", "/definitely/missing", "demo:base"
    )
    replay.restore(before)
    assert replay.reserve_batch(
        items,
        reservations,
        batch_path=batch_path,
        proposal_cost_provider_credits=0.3,
    ) == batch
    assert replay.snapshot() == generator.snapshot()

    seen = []

    def fake_pipeline(*args, **kwargs):
        seen.append(args[5])
        return _artifact(args[5]), 0.25, None

    monkeypatch.setattr(generator_module, "realize_and_build", fake_pipeline)
    candidate, cost = generator.realize_reservation(batch[0])

    assert seen == [reservations[0].candidate_id]
    assert cost == 0.25
    assert candidate["candidate_id"] == reservations[0].candidate_id
    assert candidate["provenance"]["campaign_id"] == "protocol/rep/random/demo"
    assert candidate["provenance"]["attempt_id"] == "e0000-a00"

    completion = tmp_path / "random-batch.complete.json"
    generator.complete_reserved_batch(
        batch_path,
        [
            {
                "candidate_id": reservations[0].candidate_id,
                "candidate": candidate,
                "cost_provider_credits": 0.25,
                "error": None,
            },
            {
                "candidate_id": reservations[1].candidate_id,
                "candidate": None,
                "cost_provider_credits": 0.1,
                "error": "build failed",
            },
        ],
        completion_path=completion,
    )
    accounted = generator.snapshot()
    assert accounted["digest"] == ["one"]
    assert accounted["counters"] == {"batches": 1, "skipped": 1}
    assert accounted["cost_provider_credits"] == 0.65
    generator.complete_reserved_batch(
        batch_path,
        [
            {
                "candidate_id": reservations[0].candidate_id,
                "candidate": candidate,
                "cost_provider_credits": 0.25,
                "error": None,
            },
            {
                "candidate_id": reservations[1].candidate_id,
                "candidate": None,
                "cost_provider_credits": 0.1,
                "error": "build failed",
            },
        ],
        completion_path=completion,
    )
    assert generator.snapshot() == accounted


def test_greybox_reserves_energy_sequentially_from_one_frozen_scheduler(
    tmp_path, monkeypatch
):
    generator = greybox_module.GreyboxGenerator(
        "demo", "/definitely/missing", "demo:base"
    )
    first = {
        "cand": {
            "candidate_id": "seed-a",
            "provenance": {"task_nl": "a", "env_nl": "a-env"},
        },
        "seq": ["bash:a"],
        "energy": 2,
        "base_energy": 2,
    }
    second = {
        "cand": {
            "candidate_id": "seed-b",
            "provenance": {"task_nl": "b", "env_nl": "b-env"},
        },
        "seq": ["bash:b"],
        "energy": 1,
        "base_energy": 1,
    }
    generator.corpus.extend([first, second])
    generator.queue.extend([second, first])
    reservations = make_reservations(
        "protocol/rep/greybox/demo",
        [(f"e{i:04d}", f"e{i:04d}-a00") for i in range(3)],
        epoch=4,
    )

    before = generator.snapshot()
    batch_path = tmp_path / "greybox-batch.json"
    batch = generator.reserve_mutations(reservations, batch_path=batch_path)

    assert [item["seed"]["cand"]["candidate_id"] for item in batch] == [
        "seed-a", "seed-a", "seed-b"
    ]
    assert len({item["frozen_scheduler_hash"] for item in batch}) == 1
    assert [item["candidate_id"] for item in batch] == [
        reservation.candidate_id for reservation in reservations
    ]
    assert [seed["energy"] for seed in generator.corpus] == [0, 0]
    assert batch_path.is_file()

    replay = greybox_module.GreyboxGenerator(
        "demo", "/definitely/missing", "demo:base"
    )
    replay.restore(before)
    assert replay.reserve_mutations(reservations, batch_path=batch_path) == batch
    assert replay.snapshot() == generator.snapshot()

    monkeypatch.setattr(
        generator,
        "_mutate_reserved",
        lambda seed: ("mutated task", "mutated env", 0.0),
    )
    monkeypatch.setattr(
        greybox_module,
        "realize_and_build",
        lambda *args, **kwargs: (_artifact(args[5]), 0.1, None),
    )
    reducer_owned_state = generator.snapshot()
    candidate, cost = generator.propose_reserved(batch[0])
    assert candidate["candidate_id"] == reservations[0].candidate_id
    assert cost == 0.1
    assert candidate["provenance"]["frozen_scheduler_hash"] == batch[0][
        "frozen_scheduler_hash"
    ]
    assert generator.snapshot() == reducer_owned_state

    generator.complete_reserved_batch(
        batch_path,
        [
            {
                "candidate_id": reservations[0].candidate_id,
                "candidate": candidate,
                "cost_provider_credits": cost,
                "error": None,
            },
            {
                "candidate_id": reservations[1].candidate_id,
                "candidate": None,
                "cost_provider_credits": 0.2,
                "error": "mutation failed",
            },
            {
                "candidate_id": reservations[2].candidate_id,
                "candidate": None,
                "cost_provider_credits": 0.3,
                "error": "build failed",
            },
        ],
        completion_path=tmp_path / "greybox-batch.complete.json",
    )
    snapshot = generator.snapshot()
    assert snapshot["stats"]["mutations"] == 3
    assert snapshot["stats"]["skipped_builds"] == 2
    assert snapshot["cost_provider_credits"] == 0.6


def test_random_epoch_isolates_generation_failures_and_replays_without_work(
    tmp_path, monkeypatch
):
    generator = generator_module.RandomGenerator(
        "demo", "/definitely/missing", "demo:base", max_parallel=2
    )
    reservations = make_reservations(
        "protocol/rep/random/demo",
        [("e0000", "e0000-a00"), ("e0001", "e0001-a00")],
        epoch=0,
    )
    monkeypatch.setattr(
        generator_module,
        "propose_batch",
        lambda *args, **kwargs: (
            [
                {"summary": "one", "task": "task one", "env": "env one"},
                {"summary": "two", "task": "task two", "env": "env two"},
            ],
            {"cost_provider_credits": 0.2},
        ),
    )
    calls = []

    def realize(record):
        calls.append(record["candidate_id"])
        if record["candidate_id"] == reservations[1].candidate_id:
            raise generator_module.GenerationFailure(
                "second realization failed", reason="realization-failure"
            )
        return {
            "candidate_id": record["candidate_id"],
            "provenance": {**record["provenance"], "source": "random"},
        }, 0.1

    monkeypatch.setattr(generator, "realize_reservation", realize)
    results = generator.propose_epoch(
        reservations, batch_dir=tmp_path / "random-epoch"
    )

    assert [result["candidate"] is not None for result in results] == [True, False]
    assert results[1]["error"]["reason"] == "realization-failure"
    assert generator.snapshot()["counters"] == {"batches": 1, "skipped": 1}
    assert generator.cost_provider_credits == 0.3

    monkeypatch.setattr(
        generator,
        "realize_reservation",
        lambda record: (_ for _ in ()).throw(AssertionError("replayed work")),
    )
    assert generator.propose_epoch(
        reservations, batch_dir=tmp_path / "random-epoch"
    ) == results
    assert sorted(calls) == sorted(
        reservation.candidate_id for reservation in reservations
    )


def test_random_epoch_persists_exact_realization_failure_and_its_cost(
    tmp_path, monkeypatch
):
    generator = generator_module.RandomGenerator(
        "demo", "/definitely/missing", "demo:base", max_parallel=1
    )
    reservations = make_reservations(
        "protocol/rep/random/demo", [("e0000", "e0000-a00")], epoch=0
    )
    monkeypatch.setattr(
        generator_module,
        "propose_batch",
        lambda *args, **kwargs: (
            [{"summary": "one", "task": "task", "env": "env"}],
            {"cost_provider_credits": 0.2},
        ),
    )
    monkeypatch.setattr(
        generator_module,
        "realize_and_build",
        lambda *args, **kwargs: (
            None,
            0.125,
            "build failed: exact compiler diagnostic",
        ),
    )

    results = generator.propose_epoch(
        reservations, batch_dir=tmp_path / "random-exact-error"
    )

    assert results[0]["candidate"] is None
    assert results[0]["error"] == {
        "type": "GenerationFailure",
        "reason": "realization-failure",
        "message": "build failed: exact compiler diagnostic",
    }
    assert generator.cost_provider_credits == 0.325
    completion = json.loads(
        (tmp_path / "random-exact-error/completion.json").read_text()
    )
    assert completion["payload"]["results"][0]["cost_provider_credits"] == 0.125
    assert "exact compiler diagnostic" in str(
        completion["payload"]["results"][0]["error"]
    )


def test_random_refill_accounts_for_paid_invalid_proposer_response(monkeypatch):
    generator = generator_module.RandomGenerator(
        "demo", "/definitely/missing", "demo:base", max_parallel=1
    )
    monkeypatch.setattr(
        generator_module,
        "propose_batch",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            generator_module.GenerationFailure(
                "invalid JSON",
                reason="invalid-proposer-response",
                cost_provider_credits=0.275,
            )
        ),
    )

    with pytest.raises(generator_module.GenerationFailure):
        generator.propose()

    assert generator.cost_provider_credits == 0.275


def test_greybox_reserved_failure_preserves_all_paid_call_costs(
    tmp_path, monkeypatch
):
    generator = greybox_module.GreyboxGenerator(
        "demo", "/definitely/missing", "demo:base"
    )
    reservation = {
        "schema": "greybox-mutation-reservation/1",
        "candidate_id": "candidate-a",
        "provenance": {},
        "frozen_scheduler_hash": "frozen",
        "seed": {
            "cand": {"candidate_id": "seed-a", "provenance": {}},
            "seq": [],
            "energy": 0,
        },
    }
    monkeypatch.setattr(
        generator,
        "_mutate_reserved",
        lambda seed: ("task", "env", 0.2),
    )
    monkeypatch.setattr(
        greybox_module,
        "realize_and_build",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            generator_module.GenerationFailure(
                "invalid realization",
                reason="invalid-realization-response",
                cost_provider_credits=0.3,
            )
        ),
    )

    with pytest.raises(generator_module.GenerationFailure) as caught:
        generator.propose_reserved(reservation)

    assert caught.value.cost_provider_credits == 0.5


def test_greybox_epoch_reserves_once_isolates_failures_and_replays(
    tmp_path, monkeypatch
):
    generator = greybox_module.GreyboxGenerator(
        "demo", "/definitely/missing", "demo:base"
    )
    seed = {
        "cand": {
            "candidate_id": "seed-a",
            "provenance": {"task_nl": "a", "env_nl": "a-env"},
        },
        "seq": ["bash:a"],
        "energy": 2,
        "base_energy": 2,
    }
    generator.corpus.append(seed)
    generator.queue.append(seed)
    reservations = make_reservations(
        "protocol/rep/greybox/demo",
        [("e0000", "e0000-a00"), ("e0001", "e0001-a00")],
        epoch=0,
    )
    calls = []

    def propose(record):
        calls.append(record["candidate_id"])
        if record["candidate_id"] == reservations[1].candidate_id:
            raise greybox_module.GenerationFailure(
                "second mutation failed", reason="mutation-failure"
            )
        return {
            "candidate_id": record["candidate_id"],
            "provenance": {**record["provenance"], "source": "greybox"},
        }, 0.15

    monkeypatch.setattr(generator, "propose_reserved", propose)
    results = generator.propose_epoch(
        reservations, batch_dir=tmp_path / "greybox-epoch"
    )

    assert [result["candidate"] is not None for result in results] == [True, False]
    assert results[1]["error"]["reason"] == "mutation-failure"
    snapshot = generator.snapshot()
    assert snapshot["stats"]["mutations"] == 2
    assert snapshot["stats"]["skipped_builds"] == 1
    assert snapshot["cost_provider_credits"] == 0.15

    monkeypatch.setattr(
        generator,
        "propose_reserved",
        lambda record: (_ for _ in ()).throw(AssertionError("replayed work")),
    )
    assert generator.propose_epoch(
        reservations, batch_dir=tmp_path / "greybox-epoch"
    ) == results
    assert sorted(calls) == sorted(
        reservation.candidate_id for reservation in reservations
    )


def test_guard_batch_is_branch_diverse_and_synthesis_uses_reserved_identity(
    tmp_path, monkeypatch
):
    frontier = [
        {
            "branch_key": "b1",
            "guard": {
                "branch_key": "b1",
                "parent_id": None,
                "condition": "x",
                "value_space": {"observed": ["old"]},
            },
            "mutations": ["m1", "m2"],
        },
        {
            "branch_key": "b2",
            "guard": {
                "branch_key": "b2",
                "parent_id": None,
                "condition": "y",
                "value_space": {"observed": ["old"]},
            },
            "mutations": ["m1"],
        },
    ]
    targets = guards_module.diverse_target_batch(
        frontier,
        limit=3,
        tree_version=7,
        epoch=2,
        frozen_state_hash="c" * 64,
    )
    assert [(item["branch_key"], item["mutation"]) for item in targets] == [
        ("b1", "m1"), ("b2", "m1"), ("b1", "m2")
    ]
    assert {item["tree_version"] for item in targets} == {7}
    assert {item["frozen_state_hash"] for item in targets} == {"c" * 64}

    fallback_targets = guards_module.diverse_target_batch(
        frontier[:1],
        limit=4,
        tree_version=7,
        epoch=2,
        frozen_state_hash="c" * 64,
    )
    assert [(item["kind"], item.get("fallback_slot")) for item in fallback_targets] == [
        ("target", None),
        ("target", None),
        ("fallback", 0),
        ("fallback", 1),
    ]

    reservation = make_reservations(
        "protocol/rep/skillrace/demo", [("e0010", "e0010-a00")], epoch=2
    )[0]
    monkeypatch.setattr(
        guards_module,
        "chat",
        lambda *args, **kwargs: {
            "content": json.dumps(
                {"task": "task", "env": "env", "validate_sh": "true"}
            ),
            "cost_provider_credits": 0.0,
        },
    )
    monkeypatch.setattr(
        guards_module,
        "realize_and_build",
        lambda *args, **kwargs: (_artifact(args[5]), 0.0, None),
    )
    tree = {"nodes": {}, "root_children": [], "root_edges": {}, "runs": {}}
    case, info, cost = guards_module.synthesize(
        tree,
        targets[0],
        "demo",
        "/definitely/missing",
        "demo:base",
        "model",
        tmp_path,
        proposal_id=reservation.candidate_id,
        provenance=reservation.provenance,
    )

    candidate = json.loads((tmp_path / reservation.candidate_id / "candidate.json").read_text())
    assert case == str(tmp_path / reservation.candidate_id)
    assert info["validated"] is True and cost == 0.0
    assert candidate["candidate_id"] == reservation.candidate_id
    assert candidate["provenance"]["tree_version"] == 7
    assert candidate["provenance"]["epoch"] == 2
    assert candidate["provenance"]["campaign_id"] == "protocol/rep/skillrace/demo"
    assert candidate["provenance"]["target_parent"] is None
    assert candidate["provenance"]["validation"] == {
        "validated": True,
        "validate_sh": "true",
        "target_condition": "m1",
    }


def test_synthesis_prompt_treats_intended_reach_as_diagnostic_not_a_yield_gate():
    prompt = guards_module.SYNTH_SYS.lower()

    assert "multiple coherent" in prompt
    assert "different branch" in prompt
    assert "still valuable" in prompt
    assert "diagnostic" in prompt
    assert "validate_sh" in prompt


def test_skillrace_epoch_freezes_one_branch_diverse_target_plan_before_synthesis(
    tmp_path, monkeypatch
):
    cases_dir = tmp_path / "cases"
    out_dir = tmp_path / "campaign"
    out_dir.mkdir()
    (out_dir / "tree.json").write_text(
        json.dumps({"nodes": {}, "root_children": [], "root_edges": {}, "runs": {}})
    )
    frontier = [
        {
            "branch_key": "b1",
            "guard": {
                "branch_key": "b1",
                "parent_id": "n1",
                "condition": "first guard",
            },
            "mutations": ["m1", "m1b"],
        },
        {
            "branch_key": "b2",
            "guard": {
                "branch_key": "b2",
                "parent_id": "n2",
                "condition": "second guard",
            },
            "mutations": ["m2"],
        },
    ]
    calls = {"extract": 0, "select": 0, "synthesize": [], "fallback": []}

    def extract(*args, **kwargs):
        calls["extract"] += 1
        return {"guards": {}, "tried": {}}, 0.0

    def select(items, properties, model, skill=None):
        calls["select"] += 1
        return {
            "item": items[1],
            "mutation": "m2",
            "targeted_property": "p1",
            "rationale": "highest risk",
        }, 0.05

    plan_path = out_dir / "epochs" / "epoch-0007" / "generation" / "targets.json"

    def synthesize(tree, target, *args, proposal_id=None, provenance=None, **kwargs):
        assert plan_path.is_file(), "target workers started before the plan was durable"
        calls["synthesize"].append((target["branch_key"], target["mutation"]))
        case = cases_dir / proposal_id
        case.mkdir(parents=True, exist_ok=True)
        candidate = {
            "candidate_id": proposal_id,
            "skill": "demo",
            "prompt": "exercise the skill",
            "containerfile": "FROM demo:base\n",
            "built_image": f"skillrace/{proposal_id}:built",
            "sanity": SANITY,
            "provenance": {
                **dict(provenance or {}),
                "source": "skillrace",
                "branch_key": target["branch_key"],
                "mutation": target["mutation"],
                "target_parent": target["item"]["guard"]["parent_id"],
                "targeted_property": target.get("targeted_property"),
                "frozen_state_hash": target["frozen_state_hash"],
                "validation": {
                    "validated": True,
                    "validate_sh": "true",
                    "target_condition": target["mutation"],
                },
            },
        }
        (case / "candidate.json").write_text(json.dumps(candidate))
        (case / "Dockerfile").write_text(candidate["containerfile"])
        return str(case), {"validated": True}, 0.1

    class FallbackGenerator:
        cost_provider_credits = 0.0

        def propose_epoch(self, reservations, *, batch_dir, **kwargs):
            reservations = list(reservations)
            calls["fallback"].append(
                ([item.candidate_id for item in reservations], str(batch_dir))
            )
            return [
                {
                    "candidate": {
                        "candidate_id": reservation.candidate_id,
                        "skill": "demo",
                        "prompt": "fallback",
                        "containerfile": "FROM demo:base\n",
                        "built_image": f"skillrace/{reservation.candidate_id}:built",
                        "sanity": SANITY,
                        "provenance": {
                            **dict(reservation.provenance),
                            "source": "bootstrap",
                        },
                    },
                    "source": "bootstrap",
                    "error": None,
                }
                for reservation in reservations
            ]

    monkeypatch.setattr(guards_module, "extract_all_guards", extract)
    monkeypatch.setattr(guards_module, "build_frontier", lambda state: frontier)
    monkeypatch.setattr(guards_module, "select_target", select)
    monkeypatch.setattr(guards_module, "synthesize", synthesize)
    monkeypatch.setattr(
        guards_module, "load_guard_state", lambda *args, **kwargs: ({}, tmp_path / "guards.json")
    )
    tried = []
    monkeypatch.setattr(
        guards_module,
        "mark_tried",
        lambda state, path, branch, mutation: tried.append((branch, mutation)),
    )

    component = loop_module.SkillRACEGenerator(
        "demo",
        "/definitely/missing",
        "demo:base",
        [{"id": "p1", "nl": "must work"}],
        "model",
        out_dir,
        FallbackGenerator(),
    )
    adapter = loop_module._SkillRACEEngineAdapter(component, cases_dir)
    reservations = make_reservations(
        "protocol/rep/skillrace/demo",
        [(f"e{i:04d}", f"e{i:04d}-a00") for i in range(4)],
        epoch=7,
    )
    results = adapter.propose_epoch(
        reservations,
        batch_dir=out_dir / "epochs" / "epoch-0007" / "generation",
        epoch=7,
        tree_version=7,
        frozen_state_hash="d" * 64,
        resource_pool=None,
    )

    assert calls["extract"] == 1 and calls["select"] == 1
    assert [
        (result["candidate"]["provenance"].get("branch_key"), result["source"])
        for result in results
    ] == [
        ("b2", "skillrace"),
        ("b1", "skillrace"),
        ("b1", "skillrace"),
        (None, "skillrace-fallback"),
    ]
    assert sorted(calls["synthesize"]) == [("b1", "m1"), ("b1", "m1b"), ("b2", "m2")]
    assert tried == [("b2", "m2"), ("b1", "m1"), ("b1", "m1b")]
    assert calls["fallback"][0][0] == [reservations[3].candidate_id]
    target_plan = json.loads(plan_path.read_text())
    assert target_plan["schema"] == "skillrace-target-plan/1"
    assert [item["target"]["kind"] for item in target_plan["assignments"]] == [
        "target", "target", "target", "fallback"
    ]
    assert component.stats == {"synthesized": 3, "fallbacks": 1, "synth_failures": 0}
    assert component.cost_provider_credits == 0.35

    replayed = adapter.propose_epoch(
        reservations,
        batch_dir=out_dir / "epochs" / "epoch-0007" / "generation",
        epoch=7,
        tree_version=7,
        frozen_state_hash="d" * 64,
        resource_pool=None,
    )
    assert [item["candidate"]["candidate_id"] for item in replayed] == [
        item["candidate"]["candidate_id"] for item in results
    ]
    assert calls["extract"] == 1 and calls["select"] == 1
    assert len(calls["synthesize"]) == 3
    assert component.stats == {"synthesized": 3, "fallbacks": 1, "synth_failures": 0}
    assert component.cost_provider_credits == 0.35


def test_skillrace_generation_intent_authorizes_preproposal_artifact_rollback(
    tmp_path, monkeypatch
):
    out_dir = tmp_path / "campaign"
    out_dir.mkdir()
    tree_path = out_dir / "tree.json"
    tree_path.write_text(json.dumps({"schema": "tree/1", "nodes": {}}))

    class Seed:
        cost_provider_credits = 0.0

        def snapshot(self):
            return {"schema": "seed/1"}

        def restore(self, snapshot):
            assert snapshot == {"schema": "seed/1"}

    component = loop_module.SkillRACEGenerator(
        "demo", "/definitely/missing", "demo:base", [], "model", out_dir, Seed()
    )
    before = component.snapshot()
    reservations = make_reservations(
        "protocol/rep/skillrace/demo", [("e0000", "e0000-a00")], epoch=0
    )

    def crash_after_artifact_mutation(*args, **kwargs):
        tree_path.with_suffix(".guards.json").write_text(
            json.dumps({"schema": "guards/1", "partial": True})
        )
        raise RuntimeError("crash during guard extraction")

    monkeypatch.setattr(
        guards_module, "extract_all_guards", crash_after_artifact_mutation
    )
    batch_dir = out_dir / "epochs" / "epoch-0000" / "generation"
    with pytest.raises(RuntimeError, match="guard extraction"):
        component.propose_epoch(
            reservations,
            batch_dir=batch_dir,
            cases_dir=out_dir / "cases",
            epoch=0,
            tree_version=0,
            frozen_state_hash="e" * 64,
        )
    assert (batch_dir / "generation.intent.json").is_file()
    assert tree_path.with_suffix(".guards.json").is_file()

    resumed = loop_module.SkillRACEGenerator(
        "demo", "/definitely/missing", "demo:base", [], "model", out_dir, Seed()
    )
    resumed.restore(before)

    assert not tree_path.with_suffix(".guards.json").exists()
    assert resumed.snapshot() == before
