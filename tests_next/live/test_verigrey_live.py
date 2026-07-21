from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from skillrace_next.methods.verigrey import (
    initialize_corpus,
    normalize_tool_sequence,
    observe_execution,
    select_test,
)
from skillrace_next.pipeline.stages import run_agent
from skillrace_next.records import SkillVersion
from skillrace_next.runtime.docker import RunningContainer, remove_container
from skillrace_next.storage import atomic_write_json, tree_hash
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


@pytest.mark.parametrize("model_id", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_real_verigrey_executes_full_seed_corpus_before_fifo_mutation(
    live_evidence_root: Path,
    model_id: str,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the live VeriGrey corpus contract")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "verigrey-corpus" / model_id / run_id
    evidence.mkdir(parents=True)
    image = "skillrace-next/task-fixture:test"
    subprocess.run(
        [
            "docker",
            "build",
            "-q",
            "-t",
            image,
            str(Path("tests_next/fixtures/task").resolve()),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    skill_dir = evidence / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "# Exact artifact workflow\n"
        "Create the requested artifact exactly, inspect relevant environment conditions, "
        "and verify the finished artifact with an appropriate read or shell command.\n",
        encoding="utf-8",
    )
    skill_receipt = evidence / "skill-receipt.json"
    atomic_write_json(skill_receipt, {"source": "live VeriGrey corpus fixture"})
    skill = SkillVersion(
        skill_id="live-verigrey-corpus",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id=model_id,
        receipt_path=skill_receipt,
    )
    properties = [
        {
            "property_id": "P1",
            "description": "The requested artifact exists with the exact requested content.",
        },
        {
            "property_id": "P2",
            "description": "The agent verifies the completed artifact before finishing.",
        },
    ]
    base = live_config(evidence, {"proposer": 5, "weak_agent": 6})
    config = replace(
        base,
        experiment_id=f"live-verigrey-corpus-{model_id}",
        methods=("verigrey",),
        provider="lab",
        model_id=model_id,
        iteration_budget=3,
        output_root=evidence,
        timeouts={**base.timeouts, "provider": 240, "pi": 240, "docker": 600},
    )

    state = initialize_corpus(
        skill,
        properties,
        config,
        evidence / "initial-corpus",
    )
    atomic_write_json(evidence / "state-before-execution.json", state)
    assert state["phase"] == "seeding"
    assert [item["seed_id"] for item in state["corpus"]] == ["seed-P1", "seed-P2"]
    assert all(item["status"] == "pending" for item in state["corpus"])
    assert all(
        json.loads(
            Path(item["test_case"]["nl_check_path"]).read_text(encoding="utf-8")
        )
        == properties
        for item in state["corpus"]
    )

    def execute(case, destination: Path):
        record = run_agent(skill, case, config, destination)
        try:
            assert record.termination_status == "completed"
            sequence = normalize_tool_sequence(record.trace_path)
            assert sequence
            return record, sequence
        finally:
            cleanup = remove_container(
                RunningContainer(
                    record.container_id,
                    f"verigrey-{case.test_id}",
                    record.image_id,
                )
            )
            atomic_write_json(
                destination / "cleanup.json",
                {
                    "success": cleanup.success,
                    "removed": cleanup.removed,
                    "stderr": cleanup.stderr,
                },
            )
            assert cleanup.success and cleanup.removed

    for index, expected_seed_id in enumerate(("seed-P1", "seed-P2"), 1):
        case = select_test(
            state,
            skill,
            properties,
            config,
            evidence / "selections" / str(index),
        )
        assert state["current_selection"]["seed_id"] == expected_seed_id
        record, sequence = execute(case, evidence / "executions" / str(index))
        state = observe_execution(state, sequence)
        atomic_write_json(evidence / f"state-after-seed-{index}.json", state)
        assert state["observations"][-1]["test_id"] == record.test_id

    assert state["phase"] == "mutation"
    assert state["execution_count"] == 2
    expected_parent = state["queue"][0]
    mutation = select_test(
        state,
        skill,
        properties,
        config,
        evidence / "mutation-selection",
    )
    selection = dict(state["current_selection"])
    assert selection["phase"] == "mutation"
    assert selection["parent_seed_id"] == expected_parent
    assert 1 <= selection["assigned_energy"] <= 3
    mutation_receipt = json.loads(
        mutation.proposal_receipt.read_text(encoding="utf-8")
    )
    assert mutation_receipt["parent_seed_id"] == expected_parent
    assert mutation_receipt["temperature"] == 1.0
    pi_receipt = json.loads(
        Path(mutation_receipt["pi_receipt_path"]).read_text(encoding="utf-8")
    )
    assert pi_receipt["provider"] == "lab"
    assert pi_receipt["model"] == model_id
    assert pi_receipt["temperature"] == 1.0
    mutation_record, mutation_sequence = execute(
        mutation, evidence / "mutation-execution"
    )
    state = observe_execution(state, mutation_sequence)
    atomic_write_json(evidence / "final-state.json", state)
    observation = state["observations"][-1]
    assert observation["phase"] == "mutation"
    assert observation["parent_seed_id"] == expected_parent
    assert observation["test_id"] == mutation_record.test_id
    assert isinstance(observation["corpus_admitted"], bool)
    assert state["execution_count"] == config.iteration_budget
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
