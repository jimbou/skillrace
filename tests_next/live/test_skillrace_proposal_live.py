from datetime import UTC, datetime
import json
import os
from pathlib import Path
import uuid

import pytest

from skillrace_next.methods.skillrace import propose_test, select_unreached_branch
from skillrace_next.records import SkillVersion
from skillrace_next.storage import atomic_write_json, tree_hash
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


def latest_real_tree() -> Path:
    root = Path("out/live-contracts/tree-merger")
    for candidate in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
        tree_path = candidate / "merge" / "tree.json"
        receipt_path = candidate / "merge" / "tree-merge.json"
        if tree_path.is_file() and receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("alignment_receipt_path"):
                return candidate
    pytest.fail("a successful real Yunwu tree merge is required")


def test_real_yunwu_proposes_valid_test_for_selected_unreached_branch(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("yunwu_key")
    if not secret:
        pytest.skip("yunwu_key is required for the SkillRACE proposal contract")
    source = latest_real_tree()
    tree = json.loads((source / "merge" / "tree.json").read_text(encoding="utf-8"))
    selected = select_unreached_branch(tree)
    assert selected is not None
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "skillrace-proposer" / run_id
    skill_dir = evidence / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Exact file workflow\nUse tools to create and verify exactly requested files.\n",
        encoding="utf-8",
    )
    skill_receipt = evidence / "skill-receipt.json"
    atomic_write_json(skill_receipt, {"source": "live fixture"})
    skill = SkillVersion(
        skill_id="live-branch-skill",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v3.2",
        receipt_path=skill_receipt,
    )

    proposed = propose_test(
        tree,
        skill,
        live_config(evidence, {"proposer": 4}),
    )

    assert proposed.validation_status == "valid"
    assert proposed.container_image_id.startswith("sha256:")
    receipt = json.loads(proposed.proposal_receipt.read_text(encoding="utf-8"))
    assert receipt["target_node_id"] == selected["node_id"]
    assert receipt["target_reach_status"] in {"unreached", "reasoning_unexplored"}
    pi_receipt = json.loads(Path(receipt["pi_receipt_path"]).read_text(encoding="utf-8"))
    assert pi_receipt["provider"] == "yunwu"
    assert pi_receipt["model"] == "deepseek-v3.2"
    assert pi_receipt["status"] == "completed"
    assert selected["purpose"] in proposed.nl_check_path.read_text(encoding="utf-8")
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
