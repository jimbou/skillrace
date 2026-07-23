from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from skillrace_next.methods.reasoning_tree import (
    empty_tree,
    merge_episodes,
    validate_tree,
)
from skillrace_next.runtime.pi import PiRequest, PiResult
from tests_next.unit.test_test_cases import config_for


def episodes_for_run() -> list[dict[str, object]]:
    return [
        {
            "episode_id": "episode-1",
            "start_call": 1,
            "end_call": 2,
            "purpose": "inspect the workspace",
            "what_it_did": "listed files and read the configuration",
            "outcome": "the configuration was found",
            "opening_reasoning": "First inspect the available project files.",
        },
        {
            "episode_id": "episode-2",
            "start_call": 3,
            "end_call": 4,
            "purpose": "run the tests",
            "what_it_did": "executed pytest",
            "outcome": "the tests failed on one assertion",
            "opening_reasoning": "The project is understood, so run its tests.",
        },
    ]


def tree_with_one_run(tmp_path: Path) -> dict[str, object]:
    merged, cache = merge_episodes(
        empty_tree(),
        episodes_for_run(),
        "run-1",
        [{"failure_id": "failure-1", "episode_id": "episode-2"}],
        {},
        replace(config_for(tmp_path), role_budgets={"tree_alignment": 4}),
        tmp_path / "merge",
        run_meta={"trace_path": "runs/run-1/trace.jsonl"},
        pi_runner=lambda request: (_ for _ in ()).throw(
            AssertionError("first-run placement must not call Pi")
        ),
    )
    assert cache == {}
    return merged


def test_empty_tree_is_exact_behavior_tree_record() -> None:
    assert empty_tree() == {
        "schema": "behavior-tree/2",
        "runs": {},
        "next_id": 0,
        "root_children": [],
        "root_edges": {},
        "nodes": {},
    }
    assert validate_tree(empty_tree()) == empty_tree()


def test_deterministic_first_run_creates_grounded_chain_without_pi(
    tmp_path: Path,
) -> None:
    requests: list[PiRequest] = []

    def forbidden_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        raise AssertionError("deterministic placement must not call Pi")

    episodes = episodes_for_run()
    merged, cache = merge_episodes(
        empty_tree(),
        episodes,
        "run-1",
        [{"failure_id": "failure-1", "episode_id": "episode-2"}],
        {},
        replace(config_for(tmp_path), role_budgets={"tree_alignment": 4}),
        tmp_path / "merge",
        run_meta={"trace_path": "runs/run-1/trace.jsonl"},
        pi_runner=forbidden_pi,
    )

    assert requests == []
    assert cache == {}
    assert merged["runs"] == {
        "run-1": {"trace_path": "runs/run-1/trace.jsonl"}
    }
    assert merged["next_id"] == 2
    assert merged["root_children"] == ["n0"]
    assert merged["nodes"]["n0"]["children"] == ["n1"]
    assert merged["nodes"]["n0"]["members"] == [
        {
            "run_id": "run-1",
            "episode_id": "episode-1",
            "purpose": episodes[0]["purpose"],
            "what_it_did": episodes[0]["what_it_did"],
            "outcome": episodes[0]["outcome"],
            "opening_reasoning": episodes[0]["opening_reasoning"],
        }
    ]
    assert merged["root_edges"]["n0"] == [
        {
            "run_id": "run-1",
            "in_outcome": None,
            "reasoning": episodes[0]["opening_reasoning"],
        }
    ]
    assert merged["nodes"]["n0"]["edges"]["n1"] == [
        {
            "run_id": "run-1",
            "in_outcome": episodes[0]["outcome"],
            "reasoning": episodes[1]["opening_reasoning"],
        }
    ]
    assert merged["nodes"]["n1"]["failure_ids"] == ["failure-1"]
    assert merged["nodes"]["n0"]["what_it_did_variants"] == [
        {"text": episodes[0]["what_it_did"], "run_ids": ["run-1"]}
    ]
    assert validate_tree(merged) == merged


def test_merge_rejects_invalid_failure_link(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="failure link"):
        merge_episodes(
            empty_tree(),
            episodes_for_run(),
            "run-1",
            [{"failure_id": "failure-1", "episode_id": "unknown"}],
            {},
            replace(config_for(tmp_path), role_budgets={"tree_alignment": 4}),
            tmp_path / "merge",
        )


def test_validate_tree_rejects_cycle(tmp_path: Path) -> None:
    tree = tree_with_one_run(tmp_path)
    tree["nodes"]["n1"]["children"] = ["n0"]
    tree["nodes"]["n1"]["edges"] = {
        "n0": [
            {
                "run_id": "run-1",
                "in_outcome": "the tests failed on one assertion",
                "reasoning": "Revisit the inspection.",
            }
        ]
    }
    with pytest.raises(ValueError, match="cycle"):
        validate_tree(tree)


def test_validate_tree_rejects_unreachable_node(tmp_path: Path) -> None:
    tree = tree_with_one_run(tmp_path)
    tree["runs"]["run-2"] = {}
    tree["nodes"]["n2"] = {
        "id": "n2",
        "purpose": "orphaned work",
        "what_it_did_variants": [{"text": "did work", "run_ids": ["run-2"]}],
        "runs": ["run-2"],
        "members": [
            {
                "run_id": "run-2",
                "episode_id": "episode-1",
                "purpose": "orphaned work",
                "what_it_did": "did work",
                "outcome": "work completed",
                "opening_reasoning": "Do isolated work.",
            }
        ],
        "children": [],
        "edges": {},
        "reach_status": "reached",
        "failure_ids": [],
    }
    tree["next_id"] = 3
    with pytest.raises(ValueError, match="unreachable"):
        validate_tree(tree)


def test_validate_tree_rejects_unknown_child(tmp_path: Path) -> None:
    tree = tree_with_one_run(tmp_path)
    tree["nodes"]["n1"]["children"] = ["missing"]
    tree["nodes"]["n1"]["edges"] = {"missing": []}
    with pytest.raises(ValueError, match="unknown child"):
        validate_tree(tree)


def test_validate_tree_rejects_duplicate_membership(tmp_path: Path) -> None:
    tree = tree_with_one_run(tmp_path)
    tree["nodes"]["n1"]["members"].append(
        deepcopy(tree["nodes"]["n0"]["members"][0])
    )
    with pytest.raises(ValueError, match="duplicate membership"):
        validate_tree(tree)


def test_validate_tree_rejects_malformed_transition(tmp_path: Path) -> None:
    tree = tree_with_one_run(tmp_path)
    del tree["root_edges"]["n0"][0]["reasoning"]
    with pytest.raises(ValueError, match="transition"):
        validate_tree(tree)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda tree: tree["nodes"]["n0"]["what_it_did_variants"][0].__setitem__(
                "run_ids", []
            ),
            "variant",
        ),
        (
            lambda tree: tree["nodes"]["n0"].__setitem__(
                "reach_status", "speculative"
            ),
            "reach status",
        ),
    ],
)
def test_validate_tree_rejects_invalid_variant_or_reach_status(
    tmp_path: Path, mutation, error: str
) -> None:
    tree = tree_with_one_run(tmp_path)
    mutation(tree)
    with pytest.raises(ValueError, match=error):
        validate_tree(tree)
