from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import uuid

import pytest

from skillrace_next.methods.skillrace import (
    create_diversity_plan,
    materialize_initial_test,
)
from skillrace_next.records import SkillVersion
from skillrace_next.storage import atomic_write_json, tree_hash
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


PROPERTIES = [
    {
        "property_id": "P1",
        "description": "The requested artifact is created under /workspace.",
    },
    {
        "property_id": "P2",
        "description": "The artifact exactly implements the visible task requirements.",
    },
]


def test_real_deepseek_plans_ten_seeds_and_materializes_the_first(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the live SkillRACE initializer")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "skillrace-initializer" / run_id
    skill_dir = evidence / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Exact artifact workflow\n"
        "Read the complete request, inspect the environment, create every requested file "
        "under /workspace, and verify exact observable behavior before finishing.\n",
        encoding="utf-8",
    )
    skill_receipt = evidence / "skill-receipt.json"
    atomic_write_json(skill_receipt, {"source": "live fixture"})
    skill = SkillVersion(
        skill_id="live-initializer-skill",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v4-flash",
        receipt_path=skill_receipt,
    )
    config = replace(
        live_config(evidence, {"proposer": 6}),
        provider="lab",
        model_id="deepseek-v4-flash",
        output_root=evidence,
        timeouts={
            **live_config(evidence, {"proposer": 6}).timeouts,
            "pi": 600,
            "docker": 600,
        },
    )

    plan = create_diversity_plan(
        skill, PROPERTIES, config, evidence / "plan"
    )
    proposed = materialize_initial_test(
        plan,
        0,
        skill,
        PROPERTIES,
        config,
        evidence / "first-seed",
    )

    assert len(plan["descriptions"]) == 10
    assert len({item["task"] for item in plan["descriptions"]}) == 10
    assert proposed.validation_status == "valid"
    receipt = json.loads(proposed.proposal_receipt.read_text(encoding="utf-8"))
    assert receipt["phase"] == "initial_seed"
    assert receipt["seed_id"] == "seed-01"
    assert receipt["model"] == "deepseek-v4-flash"
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
