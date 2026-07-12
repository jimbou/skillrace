from __future__ import annotations

import pytest

from skillrace.parallel_campaign import freeze_adaptive_state, plan_epoch


def test_frozen_adaptive_state_is_deeply_immutable_and_content_addressed():
    original = {"tree": {"nodes": ["n1"]}, "guards": {"b1": ["m1"]}}
    frozen = freeze_adaptive_state(tree_version=9, artifacts=original)
    original["tree"]["nodes"].append("changed")

    assert frozen.tree_version == 9
    assert tuple(frozen.artifacts["tree"]["nodes"]) == ("n1",)
    assert len(frozen.state_hash) == 64
    with pytest.raises(TypeError):
        frozen.artifacts["tree"]["nodes"][0] = "changed"


def test_skillrace_epoch_uses_one_version_unique_targets_and_all_bounds():
    targets = [
        {"branch_key": "b1", "mutation": "m1"},
        {"branch_key": "b1", "mutation": "m1", "duplicate": True},
        {"branch_key": "b1", "mutation": "m2"},
        {"branch_key": "b2", "mutation": "m1"},
        {"branch_key": "b3", "mutation": "m1"},
    ]
    jobs = plan_epoch(
        "skillrace",
        targets,
        epoch=4,
        tree_version=9,
        limit=5,
        remaining_budget=3,
        agent_slots=2,
        frozen_state_hash="a" * 64,
    )

    assert len(jobs) == 2
    assert {job["tree_version"] for job in jobs} == {9}
    assert {job["epoch"] for job in jobs} == {4}
    assert {job["frozen_state_hash"] for job in jobs} == {"a" * 64}
    assert len({(job["branch_key"], job["mutation"]) for job in jobs}) == 2


def test_epoch_planning_rejects_unversioned_skillrace_and_keeps_random_slots():
    with pytest.raises(ValueError, match="tree version"):
        plan_epoch(
            "skillrace",
            [{"branch_key": "b1", "mutation": "m1"}],
            epoch=0,
            tree_version=None,
            limit=1,
            frozen_state_hash="a" * 64,
        )

    jobs = plan_epoch(
        "random",
        [{"slot": value} for value in range(8)],
        epoch=1,
        tree_version=None,
        limit=4,
        remaining_budget=2,
        agent_slots=3,
    )
    assert [job["slot"] for job in jobs] == [0, 1]
    with pytest.raises(TypeError):
        jobs[0]["slot"] = 99
