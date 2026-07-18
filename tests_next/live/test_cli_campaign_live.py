from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import uuid

import pytest

from skillrace_next import cli
from skillrace_next.records import TestCase as CaseRecord
from skillrace_next.storage import atomic_write_json, file_hash, tree_hash
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


def _run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def _write_config(evidence: Path, part: str, iterations: int) -> Path:
    base = live_config(
        evidence,
        {
            "proposer": 4,
            "weak_agent": 4,
            "patcher": 10,
            "segmenter": 4,
            "tree_alignment": 4,
            "skill_generator": 6,
        },
    )
    config = replace(
        base,
        experiment_id=f"cli-{part}-deepseek-v4-flash",
        part=part,
        methods=("random", "verigrey", "skillrace"),
        provider="lab",
        model_id="deepseek-v4-flash",
        iteration_budget=iterations,
        heldout_repetitions=1,
        network_policy="host",
        suite_path=evidence,
        scenario_path=evidence / "scenario.md",
        output_root=evidence / "run",
        timeouts={**base.timeouts, "pi": 240, "patch": 240},
    )
    path = evidence / "config.json"
    atomic_write_json(path, config.to_dict())
    return path


def _assert_no_secret(evidence: Path, secret: str) -> None:
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")


def test_real_part1_cli_generates_and_runs_one_test_per_method(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the Part I CLI contract")
    evidence = live_evidence_root / "cli-part1" / "deepseek-v4-flash" / _run_id()
    evidence.mkdir(parents=True)
    config = _write_config(evidence, "part1", 1)
    s0 = evidence / "input" / "s0"
    s0.mkdir(parents=True)
    (s0 / "SKILL.md").write_text(
        "---\nname: exact-artifact\ndescription: Create exact requested local artifacts.\n---\n"
        "# Exact artifact\nRead the task, create the requested artifact, then read it back and "
        "correct any mismatch before stopping.\n",
        encoding="utf-8",
    )
    receipt = evidence / "input" / "s0-receipt.json"
    atomic_write_json(receipt, {"source": "real CLI Part I input"})
    properties = evidence / "input" / "properties.json"
    atomic_write_json(
        properties,
        [
            {
                "property_id": "P1",
                "description": (
                    "The artifact requested by the generated task exists and exactly "
                    "matches the task's observable content requirement."
                ),
            }
        ],
    )

    assert cli.main(
        [
            "part1",
            "--config",
            str(config),
            "--s0-dir",
            str(s0),
            "--s0-receipt",
            str(receipt),
            "--skill-id",
            "exact-artifact",
            "--properties",
            str(properties),
            "--live",
        ]
    ) == 0

    summary = json.loads(
        (evidence / "run" / "campaign" / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["s0_hash"] == tree_hash(s0)
    for method in ("random", "verigrey", "skillrace"):
        iteration = evidence / "run" / "campaign" / "methods" / method / "runs" / "0"
        assert (iteration / "execution" / "run.json").is_file()
        assert (iteration / "checks" / "results" / "check_results.json").is_file()
    assert json.loads((evidence / "run" / "command.json").read_text())["status"] == "completed"
    _assert_no_secret(evidence, secret)


def test_real_part2_cli_generates_tests_then_opens_hidden_test(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the Part II CLI contract")
    evidence = live_evidence_root / "cli-part2" / "deepseek-v4-flash" / _run_id()
    evidence.mkdir(parents=True)
    config = _write_config(evidence, "part2", 2)
    scenario = evidence / "scenario.md"
    scenario.write_text(
        "Create a reliable coding-agent skill for self-contained local file tasks. The "
        "agent must follow exact path and content requirements, inspect any supplied local "
        "inputs, write the requested artifact, and read it back before stopping.\n",
        encoding="utf-8",
    )
    hidden_root = evidence / "hidden" / "exact-marker"
    environment = hidden_root / "environment"
    environment.mkdir(parents=True)
    (environment / "Dockerfile").write_text(
        "FROM skillrace-next/task-fixture:test\nWORKDIR /workspace\n",
        encoding="utf-8",
    )
    atomic_write_json(environment / "sanity.json", {"status": "pass"})
    prompt = hidden_root / "prompt.txt"
    prompt.write_text(
        "Create /workspace/result.txt containing exactly HIDDEN_CLI_OK with no surrounding "
        "whitespace, then read it back before stopping.\n",
        encoding="utf-8",
    )
    nl_checks = hidden_root / "nl_checks.json"
    atomic_write_json(
        nl_checks,
        [
            {
                "property_id": "P1",
                "description": "result.txt contains exactly HIDDEN_CLI_OK.",
            }
        ],
    )
    receipt = hidden_root / "proposal.json"
    atomic_write_json(receipt, {"source": "hidden CLI contract"})
    record = CaseRecord(
        test_id="hidden-exact-marker",
        prompt_path=Path("prompt.txt"),
        prompt_hash=file_hash(prompt),
        environment_directory=Path("environment"),
        environment_hash=tree_hash(environment),
        nl_check_path=Path("nl_checks.json"),
        nl_check_hash=file_hash(nl_checks),
        origin_method="heldout",
        proposal_receipt=Path("proposal.json"),
        validation_status="pending",
        validation_diagnostic="",
        container_image_id="",
    )
    hidden_record = hidden_root / "test-case.json"
    atomic_write_json(hidden_record, record.to_dict())

    assert cli.main(
        [
            "part2",
            "--config",
            str(config),
            "--scenario",
            str(scenario),
            "--heldout-test",
            str(hidden_record),
            "--live",
        ]
    ) == 0

    campaign = evidence / "run" / "campaign"
    summary = json.loads((campaign / "summary.json").read_text(encoding="utf-8"))
    assert len(summary["steps"]) + len(summary["missed_slots"]) == 6
    assert [row["method"] for row in summary["heldout_evaluations"]] == [
        "s0",
        "random",
        "verigrey",
        "skillrace",
    ]
    for method in ("random", "verigrey", "skillrace"):
        steps = [step for step in summary["steps"] if step["method"] == method]
        missed = [slot for slot in summary["missed_slots"] if slot["method"] == method]
        assert sorted(
            [step["iteration"] for step in steps]
            + [slot["iteration"] for slot in missed]
        ) == [0, 1]
        for iteration in [step["iteration"] for step in steps]:
            iteration_root = campaign / "methods" / method / "iterations" / str(iteration)
            assert (iteration_root / "execution" / "run.json").is_file()
            assert (iteration_root / "checks" / "results" / "check_results.json").is_file()
    assert len(list((evidence / "run").rglob("nl_checks.json"))) >= 7
    assert json.loads((evidence / "run" / "command.json").read_text())["status"] == "completed"
    _assert_no_secret(evidence, secret)
