from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import uuid

import pytest

from skillrace_next.methods.skillrace import build_edge_index, propose_test
from skillrace_next.records import SkillVersion
from skillrace_next.storage import atomic_write_json, tree_hash
from tests_next.live.test_tree_merge_live import live_config
from tests_next.unit.test_edge_selector import long_observed_tree


pytestmark = pytest.mark.live


PROPERTIES = [
    {
        "property_id": "P1",
        "description": "The requested artifact exactly implements the visible requirements.",
    },
    {
        "property_id": "P2",
        "description": "The agent verifies the artifact's observable behavior before finishing.",
    },
]


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_real_pi_selects_and_mutates_one_edge_from_a_long_tree(
    model: str,
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the live edge selector")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "skillrace-edge-selector" / model / run_id
    skill_dir = evidence / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Exact CLI artifact workflow\n"
        "Read the complete request, inspect the supplied environment, implement every "
        "requested file under /workspace, and verify observable behavior before finishing.\n",
        encoding="utf-8",
    )
    skill_receipt = evidence / "skill-receipt.json"
    atomic_write_json(skill_receipt, {"source": "live long-tree fixture"})
    skill = SkillVersion(
        skill_id="live-edge-selector-skill",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id=model,
        receipt_path=skill_receipt,
    )
    config = replace(
        live_config(evidence, model),
        role_budgets={"proposer": 8},
        output_root=evidence,
        timeouts={
            **live_config(evidence, model).timeouts,
            "provider": 600,
            "docker": 600,
        },
    )
    tree = long_observed_tree()
    atomic_write_json(evidence / "long-tree.json", tree)

    proposed = propose_test(tree, skill, PROPERTIES, config)

    assert len(build_edge_index(tree)) == 139
    assert proposed.validation_status == "valid"
    receipt = json.loads(proposed.proposal_receipt.read_text(encoding="utf-8"))
    edge_ids = {item["edge_id"] for item in build_edge_index(tree)}
    assert receipt["target_edge_id"] in edge_ids
    assert receipt["bug_hypothesis"].strip()
    assert receipt["mutation"].strip()
    assert receipt["why_patchable"].strip()
    selector_input = Path(receipt["selector_input_path"])
    selected_branch = json.loads(
        (selector_input / "selected-branch.json").read_text(encoding="utf-8")
    )
    assert selected_branch["target_edge"]["edge_id"] == receipt["target_edge_id"]
    assert selected_branch["target_edge"]["failures"] == 1
    assert selected_branch["target_edge"]["reasoning"]
    assert selected_branch["target_edge"]["previous_outcomes"]
    dockerfile = (
        proposed.environment_directory / "Dockerfile"
    ).read_text(encoding="utf-8")
    hidden_paths = {
        path
        for path in re.findall(
            r"/(?:usr|opt)(?:/[A-Za-z0-9._-]+)+", dockerfile
        )
        if path.startswith("/opt/")
    }
    assert hidden_paths
    visible_prompt = proposed.prompt_path.read_text(encoding="utf-8")
    assert not [path for path in hidden_paths if path in visible_prompt]
    lowered_prompt = visible_prompt.lower()
    assert not re.search(
        r"\b(?:find|locate|discover|search for)\b.{0,100}"
        r"\b(?:executable|binary|tool|utility)\b",
        lowered_prompt,
        re.DOTALL,
    )
    assert not re.search(
        r"\bdo not assume\b.{0,80}\b(?:standard )?path\b",
        lowered_prompt,
        re.DOTALL,
    )
    assert not re.search(
        r"\buse\s+(?:the\s+)?(?:find|which)\b", lowered_prompt
    )
    assert receipt["selection_reason"].strip()
    for key in ("selector_pi_receipt_path", "pi_receipt_path"):
        pi_receipt_path = Path(receipt[key])
        pi_receipt = json.loads(pi_receipt_path.read_text(encoding="utf-8"))
        assert pi_receipt["provider"] == "lab"
        assert pi_receipt["model"] == model
        assert pi_receipt["status"] == "completed"
        assert pi_receipt["allowed_tools"] == []
        events = [
            json.loads(line)
            for line in (
                pi_receipt_path.parent / "accounting" / "tool-events.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line
        ]
        assert not [item for item in events if item.get("type") == "tool_call"]
    mutator_prompt = (
        Path(receipt["pi_receipt_path"]).parent / "prompt.txt"
    ).read_text(encoding="utf-8")
    assert "ISOLATED OBSERVED BRANCH" in mutator_prompt
    assert "COMPACT EDGE INDEX" not in mutator_prompt
    assert json.loads(proposed.nl_check_path.read_text(encoding="utf-8")) == PROPERTIES
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
