from dataclasses import replace
import json
from pathlib import Path

import pytest

from skillrace_next.methods.skillrace import (
    merge_episodes,
    propose_test,
    select_unreached_branch,
)
from skillrace_next.records import SkillVersion, TestCase as CaseRecord
from skillrace_next.storage import tree_hash
from skillrace_next.runtime.pi import PiRequest, PiResult
from tests_next.unit.test_episode_creator import valid_episodes
from tests_next.unit.test_test_cases import config_for


def root_tree() -> dict[str, object]:
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
            }
        ],
        "edges": [],
    }


def existing_branch_tree() -> dict[str, object]:
    tree = root_tree()
    tree["nodes"].append(
        {
            "node_id": "alternative",
            "purpose": "Try an alternative workflow",
            "outcome": "Alternative was not reached",
            "member_run_ids": [],
            "member_episode_ids": [],
            "reach_status": "unreached",
            "failure_ids": [],
        }
    )
    tree["edges"].append(
        {
            "source_node_id": "root",
            "target_node_id": "alternative",
            "reason": "An alternative workflow may exist",
        }
    )
    return tree


def test_deterministic_new_chain_uses_no_alignment_call(tmp_path: Path) -> None:
    def forbidden_pi(request: PiRequest) -> PiResult:
        raise AssertionError("deterministic placement must not call Pi")

    merged = merge_episodes(
        root_tree(),
        valid_episodes(),
        "run-1",
        [{"failure_id": "failure-1", "episode_id": "episode-2"}],
        replace(config_for(tmp_path), role_budgets={"tree_alignment": 4}),
        tmp_path / "merge",
        forbidden_pi,
    )

    assert len(merged["nodes"]) == 3
    assert len(merged["edges"]) == 2
    created = [node for node in merged["nodes"] if node["node_id"] != "root"]
    assert created[0]["member_run_ids"] == ["run-1"]
    assert created[1]["failure_ids"] == ["failure-1"]
    assert all(edge["reason"] for edge in merged["edges"])


def test_exact_existing_nodes_gain_membership_without_losing_other_branch(
    tmp_path: Path,
) -> None:
    tree = existing_branch_tree()
    episodes = valid_episodes()
    tree["nodes"].extend(
        [
            {
                "node_id": f"existing-{index}",
                "purpose": episode["purpose"],
                "outcome": episode["outcome"],
                "member_run_ids": ["older-run"],
                "member_episode_ids": [f"older-{index}"],
                "reach_status": "reached",
                "failure_ids": [],
            }
            for index, episode in enumerate(episodes, 1)
        ]
    )
    tree["edges"].extend(
        [
            {
                "source_node_id": "alternative",
                "target_node_id": "existing-1",
                "reason": "write path",
            },
            {
                "source_node_id": "existing-1",
                "target_node_id": "existing-2",
                "reason": episodes[0]["reason_for_next"],
            },
        ]
    )

    merged = merge_episodes(
        tree,
        episodes,
        "run-1",
        [],
        replace(config_for(tmp_path), role_budgets={"tree_alignment": 4}),
        tmp_path / "merge",
        lambda request: (_ for _ in ()).throw(AssertionError("no Pi call expected")),
    )

    assert next(node for node in merged["nodes"] if node["node_id"] == "alternative") == tree["nodes"][1]
    exact = next(node for node in merged["nodes"] if node["node_id"] == "existing-1")
    assert exact["member_run_ids"] == ["older-run", "run-1"]
    assert exact["member_episode_ids"] == ["older-1", "episode-1"]
    assert tree["nodes"][2]["member_run_ids"] == ["older-run"]
    assert not any(
        edge["source_node_id"] == "root"
        and edge["target_node_id"] == "existing-1"
        for edge in merged["edges"]
    )


def test_ambiguous_first_placement_uses_one_batched_alignment_call(
    tmp_path: Path,
) -> None:
    calls: list[PiRequest] = []

    def alignment_pi(request: PiRequest) -> PiResult:
        calls.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "type": "message",
                    "id": "alignment-response",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {"parent_node_id": "alternative"}
                                ),
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    merged = merge_episodes(
        existing_branch_tree(),
        valid_episodes(),
        "run-ambiguous",
        [],
        replace(config_for(tmp_path), role_budgets={"tree_alignment": 4}),
        tmp_path / "merge",
        alignment_pi,
    )

    assert len(calls) == 1
    assert calls[0].model == "deepseek-v3.2"
    assert "Do not use Markdown fences" in calls[0].prompt_path.read_text(
        encoding="utf-8"
    )
    first_created = next(
        node for node in merged["nodes"] if "episode-1" in node["member_episode_ids"]
    )
    assert any(
        edge["source_node_id"] == "alternative"
        and edge["target_node_id"] == first_created["node_id"]
        for edge in merged["edges"]
    )


def test_ambiguous_alignment_gets_one_format_correction(tmp_path: Path) -> None:
    calls: list[PiRequest] = []
    responses = [
        "The chain is a top-level alternative.\n\n"
        "```json\n{\"parent_node_id\": \"root\"}\n```",
        '{"parent_node_id":"root"}',
    ]

    def alignment_pi(request: PiRequest) -> PiResult:
        calls.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": responses.pop(0)}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    merged = merge_episodes(
        existing_branch_tree(),
        valid_episodes(),
        "run-corrected",
        [],
        replace(config_for(tmp_path), role_budgets={"tree_alignment": 4}),
        tmp_path / "merge",
        alignment_pi,
    )

    assert len(calls) == 2
    assert "previous response was invalid" in calls[1].prompt_path.read_text(
        encoding="utf-8"
    )
    first_created = next(
        node for node in merged["nodes"] if "episode-1" in node["member_episode_ids"]
    )
    assert any(
        edge["source_node_id"] == "root"
        and edge["target_node_id"] == first_created["node_id"]
        for edge in merged["edges"]
    )


def test_duplicate_episode_membership_is_rejected(tmp_path: Path) -> None:
    tree = root_tree()
    tree["nodes"][0]["member_run_ids"] = ["run-1"]
    tree["nodes"][0]["member_episode_ids"] = ["episode-1"]

    with pytest.raises(ValueError, match="duplicate membership"):
        merge_episodes(
            tree,
            valid_episodes(),
            "run-1",
            [],
            replace(config_for(tmp_path), role_budgets={"tree_alignment": 4}),
            tmp_path / "merge",
        )


def test_select_unreached_branch_is_deterministic() -> None:
    tree = existing_branch_tree()
    tree["nodes"].append(
        {
            "node_id": "aaa-reasoning-gap",
            "purpose": "Explore a missing reasoning path",
            "outcome": "No run has explored it",
            "member_run_ids": [],
            "member_episode_ids": [],
            "reach_status": "reasoning_unexplored",
            "failure_ids": [],
        }
    )
    tree["edges"].append(
        {
            "source_node_id": "root",
            "target_node_id": "aaa-reasoning-gap",
            "reason": "reasoning path remains unexplored",
        }
    )

    selected = select_unreached_branch(tree)

    assert selected is not None
    assert selected["node_id"] == "aaa-reasoning-gap"
    for node in tree["nodes"]:
        node["reach_status"] = "reached"
    assert select_unreached_branch(tree) is None


def test_select_unreached_branch_falls_back_to_reached_failure_node() -> None:
    tree = root_tree()
    tree["nodes"].append(
        {
            "node_id": "failed-branch",
            "purpose": "Write and verify the artifact",
            "outcome": "The artifact did not satisfy its check",
            "member_run_ids": ["run-1"],
            "member_episode_ids": ["episode-1"],
            "reach_status": "reached",
            "failure_ids": ["P1-C1"],
        }
    )
    tree["edges"].append(
        {
            "source_node_id": "root",
            "target_node_id": "failed-branch",
            "reason": "The artifact step was attempted",
        }
    )

    selected = select_unreached_branch(tree)

    assert selected is not None
    assert selected["node_id"] == "failed-branch"


def test_skillrace_proposal_records_selected_branch_and_validates_test(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Fixture skill\n", encoding="utf-8")
    skill = SkillVersion(
        skill_id="fixture-skill",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v3.2",
        receipt_path=tmp_path / "skill-receipt.json",
    )
    requests: list[PiRequest] = []

    def proposal_pi(request: PiRequest) -> PiResult:
        requests.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        trace = request.output_dir / "trace.jsonl"
        trace.write_text(
            json.dumps(
                {
                    "type": "message",
                    "id": "proposal-response",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "prompt": "Exercise the alternative workflow exactly.",
                                        "check_description": "The requested alternative output exists.",
                                    }
                                ),
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        receipt = request.output_dir / "receipt.json"
        receipt.write_text("{}\n", encoding="utf-8")
        return PiResult(
            operation_id=request.operation_id,
            model=request.model,
            status="completed",
            trace_path=trace,
            usage={},
            stderr="",
            receipt_path=receipt,
            return_code=0,
            wall_seconds=0.1,
            timeout_seconds=request.timeout_seconds,
        )

    def validator(test: CaseRecord, config: object) -> CaseRecord:
        return replace(
            test,
            validation_status="valid",
            validation_diagnostic="validated",
            container_image_id="sha256:test-image",
        )

    output = tmp_path / "proposal-output"
    config = replace(
        config_for(tmp_path),
        methods=("skillrace",),
        role_budgets={"proposer": 4},
        suite_path=output,
        output_root=output,
    )

    proposed = propose_test(
        existing_branch_tree(),
        skill,
        config,
        pi_runner=proposal_pi,
        validator=validator,
    )

    assert proposed.validation_status == "valid"
    assert proposed.origin_method == "skillrace"
    receipt = json.loads(proposed.proposal_receipt.read_text(encoding="utf-8"))
    assert receipt["target_node_id"] == "alternative"
    assert receipt["pi_receipt_path"] == str(requests[0].output_dir / "receipt.json")
    proposal_prompt = requests[0].prompt_path.read_text(encoding="utf-8")
    assert "starts with an empty /workspace" in proposal_prompt
    assert "Do not use /mnt/data or /tmp" in proposal_prompt
    assert "must not add requirements" in proposal_prompt
    assert "meaningfully exercise the supplied skill" in proposal_prompt
    assert "not a substitute for skill relevance" in proposal_prompt
    assert "internally consistent" in proposal_prompt
    assert "mutually inconsistent requirements" in proposal_prompt
    assert "Try an alternative workflow" in proposal_prompt
    assert "Do not use Markdown fences" in proposal_prompt
    assert "self-contained" in proposal_prompt
    assert "The requested alternative output exists" in proposed.nl_check_path.read_text(
        encoding="utf-8"
    )
