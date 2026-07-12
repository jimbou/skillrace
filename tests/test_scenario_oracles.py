from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from skillrace.scenario_audit import (
    _run_variant,
    MatrixGrade,
    audit_test,
    evidence_state_for_grade,
    grade_oracle_matrix,
    overlay_delete_paths,
)
from skillrace.scenario_contract import ContractError, _load_evidence


ROOT = Path(__file__).parents[1] / "scenarios"


def test_matrix_requires_every_reference_criterion_to_pass():
    report = grade_oracle_matrix(
        criteria=("a", "b"),
        reference={"a": 0, "b": 1},
        starting={"a": 1, "b": 1},
        negatives={"wrong": {"a": 1, "b": 1}},
        assignments={"a": ("wrong",), "b": ("wrong",)},
    )
    assert report.reference_passed is False


def test_matrix_requires_starting_state_to_fail_at_least_one_criterion():
    report = grade_oracle_matrix(
        criteria=("a", "b"),
        reference={"a": 0, "b": 0},
        starting={"a": 0, "b": 0},
        negatives={"wrong": {"a": 1, "b": 1}},
        assignments={"a": ("wrong",), "b": ("wrong",)},
    )
    assert report.starting_rejected is False


def test_matrix_requires_each_assigned_negative_to_be_killed():
    report = grade_oracle_matrix(
        criteria=("a", "b"),
        reference={"a": 0, "b": 0},
        starting={"a": 1, "b": 1},
        negatives={"wrong": {"a": 1, "b": 0}},
        assignments={"a": ("wrong",), "b": ("wrong",)},
    )
    assert report.negative_oracles_passed is False
    assert report.survivors == (("wrong", "b"),)


def test_regex_anchoring_probes_include_terminal_newline_boundary():
    for script in sorted(ROOT.glob("regex-validate/tests/t*/checks/anchored.sh")):
        source = script.read_text(encoding="utf-8")
        assert "v0+'\\n'" in source, script


def test_sqlite_t3_and_t6_have_distinct_query_specific_second_probes():
    t3 = (ROOT / "sqlite-query/tests/t3/checks/clean.sh").read_text(encoding="utf-8")
    t6 = (ROOT / "sqlite-query/tests/t6/checks/clean.sh").read_text(encoding="utf-8")
    assert "VALUES('z',1,1.0,'N')" in t3
    assert "VALUES('z',1,1.0,'E')" in t6
    assert t3 != t6


def test_negative_overlay_delete_directive_is_relative_and_confined(tmp_path: Path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    directive = overlay / ".skillrace-delete"
    directive.write_text("test_mod.py\nsubdir/config.ini\n", encoding="utf-8")
    assert overlay_delete_paths(overlay) == ("test_mod.py", "subdir/config.ini")
    directive.write_text("../escape\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe delete path"):
        overlay_delete_paths(overlay)


def test_failed_runtime_matrix_can_never_be_serialized_as_validated():
    failed = MatrixGrade(False, True, True, ())
    passed = MatrixGrade(True, True, True, ())
    assert evidence_state_for_grade(failed) == "audit-failed"
    assert evidence_state_for_grade(passed) == "validated"


def test_runtime_audit_uses_a_fresh_container_for_every_criterion(tmp_path, monkeypatch):
    checks = tmp_path / "checks"
    checks.mkdir()
    first = checks / "first.sh"
    second = checks / "second.sh"
    first.write_text("#!/bin/sh\nexit 0\n")
    second.write_text("#!/bin/sh\nexit 0\n")
    contract = SimpleNamespace(
        root=tmp_path,
        criteria=(
            SimpleNamespace(id="first", script=first, script_sha256="a" * 64),
            SimpleNamespace(id="second", script=second, script_sha256="b" * 64),
        ),
    )
    executed_in = []

    def fake_run(command, *, timeout=120):
        if command[:2] == ["docker", "exec"] and command[-2:] in (
            ["bash", "/check/oracle/first.sh"],
            ["bash", "/check/oracle/second.sh"],
        ):
            executed_in.append(command[2])
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("skillrace.scenario_audit._run", fake_run)

    statuses, details = _run_variant(
        contract, "image", None, "starting", containers=[]
    )

    assert statuses == {"first": 0, "second": 0}
    assert len(executed_in) == 2
    assert len(set(executed_in)) == 2
    assert {row["isolation"] for row in details.values()} == {
        "fresh-container-per-criterion"
    }


def test_validated_evidence_rejects_shared_or_unrecorded_criterion_containers(tmp_path):
    evidence = tmp_path / "validation.json"
    digest = "a" * 64
    evidence.write_text(
        json.dumps(
            {
                "schema": "skillrace-oracle-evidence/1",
                "test_id": "demo/t1",
                "state": "validated",
                "contract_identity_sha256": digest,
                "reason": "old shared audit",
                "validated_at": "2026-07-12T00:00:00Z",
                "image_digest": "sha256:image",
                "docker_version": "29",
                "reference": {
                    "passed": True,
                    "criteria": {"behavior": {"exit_code": 0}},
                },
                "starting": {
                    "rejected": True,
                    "criteria": {"behavior": {"exit_code": 1}},
                },
                "negative_implementations": {
                    "wrong": {
                        "killed_assigned": True,
                        "criteria": {"behavior": {"exit_code": 1}},
                    }
                },
            }
        )
    )

    with pytest.raises(ContractError, match="fresh-container-per-criterion"):
        _load_evidence(evidence, "demo/t1", digest, ("behavior",))


@pytest.mark.docker
@pytest.mark.skipif(
    os.environ.get("SKILLRACE_RUN_DOCKER") != "1",
    reason="set SKILLRACE_RUN_DOCKER=1 for the runtime oracle gate",
)
@pytest.mark.parametrize("test_dir", sorted(ROOT.glob("*/tests/t*")))
def test_reference_and_negative_oracles_in_docker(test_dir: Path):
    report = audit_test(test_dir, persist=False)
    assert report["reference_passed"] is True
    assert report["starting_rejected"] is True
    assert report["negative_oracles_passed"] is True
