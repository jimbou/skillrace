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
from skillrace_next.pipeline.campaigns import _run, _select_test, _updated_state, _verify
from skillrace_next.records import SkillVersion
from skillrace_next.storage import atomic_write_json, tree_hash
from skillrace_next.verification.codex import command_invokes_docker
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


@pytest.mark.parametrize("model_id", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_real_model_plans_ten_seeds_and_materializes_the_first(
    live_evidence_root: Path,
    model_id: str,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the live SkillRACE initializer")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "skillrace-initializer" / model_id / run_id
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
        model_id=model_id,
        receipt_path=skill_receipt,
    )
    config = replace(
        live_config(evidence, {"proposer": 6}),
        provider="lab",
        model_id=model_id,
        output_root=evidence,
        timeouts={
            **live_config(evidence, {"proposer": 6}).timeouts,
            "provider": 600,
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
    assert receipt["model"] == model_id
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("model_id", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_real_model_executes_ten_seeds_then_uses_the_observed_tree(
    live_evidence_root: Path,
    model_id: str,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the live SkillRACE seed campaign")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "skillrace-ten-seed" / model_id / run_id
    evidence.mkdir(parents=True)
    properties = json.loads(
        Path("skillrace_next/study/part1/file-check/properties.json").read_text(
            encoding="utf-8"
        )
    )
    skill_dir = Path("skills/file-check")
    skill = SkillVersion(
        skill_id="file-check",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="input",
        model_id=model_id,
        receipt_path=Path("skillrace_next/study/part1/file-check/s0-receipt.json"),
    )
    base = live_config(
        evidence,
        {
            "proposer": 6,
            "weak_agent": 4,
            "segmenter": 4,
            "tree_alignment": 4,
            "patcher": 10,
        },
    )
    config = replace(
        base,
        experiment_id=f"live-skillrace-ten-seed-{model_id}",
        methods=("skillrace",),
        provider="lab",
        model_id=model_id,
        iteration_budget=11,
        output_root=evidence,
        timeouts={
            **base.timeouts,
            "provider": 600,
            "pi": 60,
            "docker": 600,
            "codex": 300,
        },
    )

    state: dict[str, object] = {}
    tests = []
    for slot in range(11):
        slot_dir = evidence / "runs" / f"{slot:02d}"
        selected = _select_test(
            "skillrace", state, skill, properties, config, slot_dir / "proposal"
        )
        test = selected["case"]
        assert test.validation_status == "valid"
        assert json.loads(test.nl_check_path.read_text(encoding="utf-8")) == properties
        record = _run("skillrace", skill, selected, config, slot_dir / "execution")
        _, results, manifest = _verify(
            skill, test, record, config, slot_dir / "checks"
        )
        assert {
            item["property_id"] for item in manifest["checks"]
        } | {
            item["property_id"] for item in manifest["uncovered"]
        } == {item["property_id"] for item in properties}
        state = _updated_state(
            "skillrace",
            state,
            record,
            list(results.results),
            config,
            slot_dir / "state-update",
        )
        atomic_write_json(slot_dir / "state.json", state)
        tests.append(test)

    observations = state["observations"]
    assert state["execution_count"] == 11
    assert state["phase"] == "branch"
    assert [item["phase"] for item in observations[:10]] == ["initial_seed"] * 10
    assert [item["seed_id"] for item in observations[:10]] == [
        f"seed-{index:02d}" for index in range(1, 11)
    ]
    assert observations[10]["phase"] == "branch"
    assert observations[10]["target_edge_id"]
    assert state["tree"]["edges"]
    plan_hashes = {
        json.loads(test.proposal_receipt.read_text(encoding="utf-8"))["plan_hash"]
        for test in tests[:10]
    }
    assert plan_hashes == {state["plan"]["plan_hash"]}
    branch_receipt = json.loads(tests[10].proposal_receipt.read_text(encoding="utf-8"))
    assert branch_receipt["target_edge_id"] == observations[10]["target_edge_id"]
    assert branch_receipt["selector_input_hash"]
    assert branch_receipt["temperature"] == 1.0

    cleanup_receipts = list((evidence / "runs").rglob("cleanup.json"))
    assert len(cleanup_receipts) == 11
    assert all(json.loads(path.read_text())["success"] for path in cleanup_receipts)
    codex_commands = []
    for events_path in (evidence / "runs").rglob("codex-events.jsonl"):
        for line in events_path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "command_execution":
                command = item.get("command")
                if isinstance(command, str):
                    codex_commands.append(command)
    assert codex_commands
    assert not any(command_invokes_docker(command) for command in codex_commands)
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
