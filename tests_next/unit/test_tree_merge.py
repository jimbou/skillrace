from copy import deepcopy
from dataclasses import replace
import json
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


def run_a_episodes() -> list[dict[str, object]]:
    return [
        {
            "episode_id": "a-1",
            "start_call": 1,
            "end_call": 2,
            "purpose": "inspect the workspace",
            "what_it_did": "listed files and read project configuration",
            "outcome": "the repository structure was understood",
            "opening_reasoning": "Inspect the project before making changes.",
        },
        {
            "episode_id": "a-2",
            "start_call": 3,
            "end_call": 3,
            "purpose": "run the tests",
            "what_it_did": "executed pytest",
            "outcome": "PASS: all tests passed",
            "opening_reasoning": "Now establish the test baseline.",
        },
        {
            "episode_id": "a-3",
            "start_call": 4,
            "end_call": 4,
            "purpose": "report completion",
            "what_it_did": "read the final status",
            "outcome": "the successful result was reported",
            "opening_reasoning": "The tests pass, so report the result.",
        },
    ]


def run_b_episodes() -> list[dict[str, object]]:
    return [
        {
            "episode_id": "b-1",
            "start_call": 1,
            "end_call": 1,
            "purpose": "explore the repository",
            "what_it_did": "used find to survey source files",
            "outcome": "the code layout was understood",
            "opening_reasoning": "Explore the repository before deciding what to do.",
        },
        {
            "episode_id": "b-2",
            "start_call": 2,
            "end_call": 2,
            "purpose": "execute pytest",
            "what_it_did": "executed pytest -q",
            "outcome": "FAIL: one assertion failed",
            "opening_reasoning": "With the layout understood, execute the tests.",
        },
        {
            "episode_id": "b-3",
            "start_call": 3,
            "end_call": 4,
            "purpose": "repair the failing implementation",
            "what_it_did": "edited the calculation",
            "outcome": "the calculation was corrected",
            "opening_reasoning": "The failure identifies a repairable calculation bug.",
        },
        {
            "episode_id": "b-4",
            "start_call": 5,
            "end_call": 5,
            "purpose": "execute pytest",
            "what_it_did": "executed pytest -q",
            "outcome": "PASS: all tests passed after repair",
            "opening_reasoning": "Verify the repair with the same test suite.",
        },
    ]


def pi_result(request: PiRequest, response: str) -> PiResult:
    request.output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = request.output_dir / "trace.jsonl"
    trace_path.write_text(
        json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": response}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    receipt_path = request.output_dir / "receipt.json"
    receipt_path.write_text('{"status":"completed"}\n', encoding="utf-8")
    return PiResult(
        operation_id=request.operation_id,
        model=request.model,
        status="completed",
        trace_path=trace_path,
        usage={"total_tokens": 10},
        stderr="",
        receipt_path=receipt_path,
        return_code=0,
        wall_seconds=0.1,
        timeout_seconds=request.timeout_seconds,
    )


def semantic_responder(requests: list[PiRequest]):
    def respond(request: PiRequest) -> PiResult:
        requests.append(request)
        prompt = request.prompt_path.read_text(encoding="utf-8")
        payload = json.loads(prompt.split("\n\nINPUT:\n", 1)[1])
        if ".same-purpose." in request.operation_id:
            purposes = " ".join(
                item["purpose"] for item in payload["episodes"]
            ).lower()
            same = (
                "inspect" in purposes and "explore" in purposes
            ) or (
                ("run the tests" in purposes or "test suite" in purposes)
                and "execute pytest" in purposes
            )
            response = {"same": same, "reason": "same sub-goal" if same else "different sub-goals"}
        elif ".broaden-purpose." in request.operation_id:
            purposes = " ".join(payload.values()).lower()
            response = {
                "mergeable": True,
                "purpose": (
                    "inspect and understand the repository"
                    if "inspect" in purposes or "explore" in purposes
                    else "run the test suite"
                ),
                "reason": "both episodes retain one concrete shared objective",
            }
        elif ".same-approach." in request.operation_id:
            ways = " ".join(payload["ways"]).lower()
            response = {"same": "pytest" in ways}
        else:
            raise AssertionError(f"unexpected operation: {request.operation_id}")
        return pi_result(request, json.dumps(response))

    return respond


def one_episode(
    purpose: str,
    what_it_did: str,
    *,
    episode_id: str = "episode-1",
) -> list[dict[str, object]]:
    return [
        {
            "episode_id": episode_id,
            "start_call": 1,
            "end_call": 1,
            "purpose": purpose,
            "what_it_did": what_it_did,
            "outcome": "the attempted work produced an observed result",
            "opening_reasoning": f"Start the concrete task: {purpose}.",
        }
    ]


def test_broad_purpose_is_rejected_and_creates_a_separate_root(
    tmp_path: Path,
) -> None:
    config = replace(config_for(tmp_path), role_budgets={"tree_alignment": 4})
    tree, _ = merge_episodes(
        empty_tree(),
        one_episode(
            "implement deepClone with focused tests",
            "wrote deepClone.js and deepClone.test.js",
        ),
        "run-deep-clone",
        [],
        {},
        config,
        tmp_path / "seed",
    )
    requests: list[PiRequest] = []

    def rejecting_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        if ".same-purpose." in request.operation_id:
            return pi_result(
                request,
                json.dumps(
                    {
                        "same": True,
                        "reason": "Both broadly implement JavaScript features with tests.",
                    }
                ),
            )
        if ".broaden-purpose." in request.operation_id:
            return pi_result(
                request,
                json.dumps(
                    {
                        "mergeable": False,
                        "purpose": None,
                        "reason": (
                            "The common label would only be 'implement functionality "
                            "and tests'."
                        ),
                    }
                ),
            )
        raise AssertionError(f"unexpected judgment: {request.operation_id}")

    merged, _ = merge_episodes(
        tree,
        one_episode(
            "implement findMissingNumber with focused tests",
            "wrote missing.js and missing.test.js",
        ),
        "run-missing-number",
        [],
        {},
        config,
        tmp_path / "reject",
        pi_runner=rejecting_pi,
    )

    assert merged["root_children"] == ["n0", "n1"]
    assert merged["nodes"]["n0"]["runs"] == ["run-deep-clone"]
    assert merged["nodes"]["n1"]["runs"] == ["run-missing-number"]
    assert not any(".same-approach." in item.operation_id for item in requests)


def test_concrete_purpose_broadening_admits_merge_and_preserves_detail(
    tmp_path: Path,
) -> None:
    config = replace(config_for(tmp_path), role_budgets={"tree_alignment": 4})
    tree, _ = merge_episodes(
        empty_tree(),
        one_episode(
            "repair primitive object-property recursion in deepClone",
            "added a typeof guard before recursively cloning object properties",
        ),
        "run-object-guard-a",
        [],
        {},
        config,
        tmp_path / "seed",
    )
    requests: list[PiRequest] = []

    def admitting_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        if ".same-purpose." in request.operation_id:
            return pi_result(
                request,
                json.dumps(
                    {
                        "same": True,
                        "reason": (
                            "Both repair primitive values encountered during "
                            "deepClone object recursion."
                        ),
                    }
                ),
            )
        if ".broaden-purpose." in request.operation_id:
            return pi_result(
                request,
                json.dumps(
                    {
                        "mergeable": True,
                        "purpose": "repair primitive handling in deepClone recursion",
                        "reason": (
                            "Both episodes repair the same recursive "
                            "primitive-handling defect."
                        ),
                    }
                ),
            )
        if ".same-approach." in request.operation_id:
            return pi_result(request, '{"same":true}')
        raise AssertionError(f"unexpected judgment: {request.operation_id}")

    merged, _ = merge_episodes(
        tree,
        one_episode(
            "fix primitive values during deepClone object recursion",
            "guarded non-object property values before the recursive call",
        ),
        "run-object-guard-b",
        [],
        {},
        config,
        tmp_path / "admit",
        pi_runner=admitting_pi,
    )

    assert merged["root_children"] == ["n0"]
    assert merged["nodes"]["n0"]["purpose"] == (
        "repair primitive handling in deepClone recursion"
    )
    assert merged["nodes"]["n0"]["runs"] == [
        "run-object-guard-a",
        "run-object-guard-b",
    ]
    prompts = "\n".join(
        request.prompt_path.read_text(encoding="utf-8") for request in requests
    )
    assert "same concrete component" in prompts
    assert "subset" in prompts
    assert "generic lifecycle" in prompts
    assert "actual technical method" in prompts


def test_contextual_semantic_fold_merges_prefix_and_preserves_branches(
    tmp_path: Path,
) -> None:
    config = replace(config_for(tmp_path), role_budgets={"tree_alignment": 4})
    tree_a, _ = merge_episodes(
        empty_tree(), run_a_episodes(), "run-A", [], {}, config, tmp_path / "run-a"
    )
    requests: list[PiRequest] = []
    tree_b, cache = merge_episodes(
        tree_a,
        run_b_episodes(),
        "run-B",
        [{"failure_id": "failure-B", "episode_id": "b-2"}],
        {},
        config,
        tmp_path / "run-b",
        pi_runner=semantic_responder(requests),
    )

    assert tree_b["root_children"] == ["n0"]
    assert tree_b["nodes"]["n0"]["children"] == ["n1"]
    assert {member["run_id"] for member in tree_b["nodes"]["n0"]["members"]} == {
        "run-A",
        "run-B",
    }
    assert {member["run_id"] for member in tree_b["nodes"]["n1"]["members"]} == {
        "run-A",
        "run-B",
    }
    assert tree_b["nodes"]["n0"]["purpose"] == "inspect and understand the repository"
    assert tree_b["nodes"]["n1"]["purpose"] == "run the test suite"
    assert tree_b["nodes"]["n0"]["what_it_did_variants"] == [
        {
            "text": "listed files and read project configuration",
            "run_ids": ["run-A"],
        },
        {"text": "used find to survey source files", "run_ids": ["run-B"]},
    ]
    assert tree_b["nodes"]["n1"]["what_it_did_variants"][0]["run_ids"] == [
        "run-A",
        "run-B",
    ]
    assert len(tree_b["nodes"]["n1"]["children"]) == 2
    child_purposes = {
        tree_b["nodes"][child]["purpose"]
        for child in tree_b["nodes"]["n1"]["children"]
    }
    assert child_purposes == {"report completion", "repair the failing implementation"}
    report_id = next(
        child for child in tree_b["nodes"]["n1"]["children"]
        if tree_b["nodes"][child]["purpose"] == "report completion"
    )
    repair_id = next(
        child for child in tree_b["nodes"]["n1"]["children"]
        if tree_b["nodes"][child]["purpose"] == "repair the failing implementation"
    )
    assert tree_b["nodes"]["n1"]["edges"][report_id][0]["in_outcome"] == "PASS: all tests passed"
    assert tree_b["nodes"]["n1"]["edges"][repair_id][0] == {
        "run_id": "run-B",
        "in_outcome": "FAIL: one assertion failed",
        "reasoning": "The failure identifies a repairable calculation bug.",
    }
    assert tree_b["nodes"]["n1"]["failure_ids"] == ["failure-B"]
    assert cache
    assert all(request.provider == config.provider for request in requests)
    assert all(request.model == config.model_id for request in requests)
    assert all(request.temperature == 0 for request in requests)
    assert all(request.allowed_tools == () for request in requests)
    same_prompts = [
        request.prompt_path.read_text(encoding="utf-8")
        for request in requests
        if ".same-purpose." in request.operation_id
    ]
    assert all("PASS: all tests passed" not in prompt for prompt in same_prompts)
    assert all("FAIL: one assertion failed" not in prompt for prompt in same_prompts)

    call_count = len(requests)
    tree_c, cache_c = merge_episodes(
        tree_a,
        run_b_episodes(),
        "run-C",
        [],
        cache,
        config,
        tmp_path / "run-c",
        pi_runner=semantic_responder(requests),
    )
    assert len(requests) == call_count
    assert cache_c == cache
    assert {member["run_id"] for member in tree_c["nodes"]["n0"]["members"]} == {
        "run-A",
        "run-C",
    }


def test_tree_judgment_allows_only_three_total_attempts(tmp_path: Path) -> None:
    config = replace(config_for(tmp_path), role_budgets={"tree_alignment": 4})
    tree, _ = merge_episodes(
        empty_tree(),
        run_a_episodes()[:1],
        "run-A",
        [],
        {},
        config,
        tmp_path / "seed",
    )
    attempts: list[PiRequest] = []

    def correcting_pi(request: PiRequest) -> PiResult:
        attempts.append(request)
        same_attempts = [
            item for item in attempts if ".same-purpose." in item.operation_id
        ]
        if ".same-purpose." in request.operation_id:
            responses = ["not-json", '{"same":true}', '{"same":true,"reason":"same"}']
            return pi_result(request, responses[len(same_attempts) - 1])
        if ".same-approach." in request.operation_id:
            return pi_result(request, '{"same":true}')
        raise AssertionError("identical purpose must not require broadening")

    merged, _ = merge_episodes(
        tree,
        run_a_episodes()[:1],
        "run-B",
        [],
        {},
        config,
        tmp_path / "corrected",
        pi_runner=correcting_pi,
    )

    assert len([item for item in attempts if ".same-purpose." in item.operation_id]) == 3
    assert len(merged["nodes"]["n0"]["members"]) == 2
    assert "Previous response invalid" in attempts[1].prompt_path.read_text(
        encoding="utf-8"
    )
