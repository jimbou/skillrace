from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

import skillrace.skill_eval as skill_eval_module

from skillrace.io_utils import file_hash
from skillrace.skill_eval import (
    HiddenExecutionRequest,
    derive_case,
    execute_hidden_request,
    grade_run,
    raw_execution_artifacts,
    summarize_runs,
)


def test_functional_and_strict_pass_are_distinct():
    verdicts = [
        {
            "property_id": "behavior",
            "provenance": "hidden-independent",
            "holds": True,
            "violated": False,
        },
        {
            "property_id": "fixed-no-force-push",
            "provenance": "fixed",
            "holds": False,
            "violated": True,
        },
    ]

    grade = grade_run(
        verdicts,
        execution_status="completed",
        expected_criterion_ids=("behavior",),
    )

    assert grade["status"] == "completed"
    assert grade["functional_pass"] is True
    assert grade["fixed_clean"] is False
    assert grade["strict_pass"] is False


def test_timeout_error_and_inconclusive_are_not_silent_failures():
    passing = [
        {
            "property_id": "behavior",
            "provenance": "hidden-independent",
            "holds": True,
            "violated": False,
        }
    ]
    assert grade_run(
        passing,
        execution_status="timeout",
        expected_criterion_ids=("behavior",),
    )["functional_pass"] is None
    assert grade_run(
        passing,
        execution_status="error",
        expected_criterion_ids=("behavior",),
    )["strict_pass"] is None

    unknown = [
        {
            "property_id": "behavior",
            "provenance": "hidden-independent",
            "holds": None,
            "violated": False,
        }
    ]
    grade = grade_run(
        unknown,
        execution_status="completed",
        expected_criterion_ids=("behavior",),
    )
    assert grade["status"] == "inconclusive"
    assert grade["functional_pass"] is None
    assert grade["inconclusive_criteria"] == ["behavior"]


def test_functional_pass_requires_at_least_one_functional_criterion():
    grade = grade_run(
        [
            {
                "property_id": "fixed-safe",
                "provenance": "fixed",
                "holds": True,
                "violated": False,
            }
        ],
        execution_status="completed",
        expected_criterion_ids=("behavior",),
    )

    assert grade["status"] == "inconclusive"
    assert grade["functional_pass"] is None
    assert grade["functional_criteria_count"] == 0


def test_functional_grading_refuses_oracles_without_hidden_independent_provenance():
    grade = grade_run(
        [
            {
                "property_id": "behavior",
                "provenance": "compiled-pre-run",
                "holds": True,
                "violated": False,
            }
        ],
        execution_status="completed",
        expected_criterion_ids=("behavior",),
    )

    assert grade["status"] == "inconclusive"
    assert grade["functional_pass"] is None
    assert grade["untrusted_criteria"] == ["behavior"]


@pytest.mark.parametrize(
    ("verdicts", "diagnostic"),
    [
        (
            [
                {
                    "property_id": "criterion-a",
                    "provenance": "hidden-independent",
                    "holds": True,
                    "violated": False,
                }
            ],
            "missing_criteria",
        ),
        (
            [
                {
                    "property_id": name,
                    "provenance": "hidden-independent",
                    "holds": True,
                    "violated": False,
                }
                for name in ("criterion-a", "criterion-b", "invented")
            ],
            "extra_criteria",
        ),
        (
            [
                {
                    "property_id": name,
                    "provenance": "hidden-independent",
                    "holds": True,
                    "violated": False,
                }
                for name in ("criterion-a", "criterion-a", "criterion-b")
            ],
            "duplicate_criteria",
        ),
        (
            [
                {
                    "property_id": "criterion-a",
                    "provenance": "compiled-pre-run",
                    "holds": True,
                    "violated": False,
                },
                {
                    "property_id": "criterion-b",
                    "provenance": "hidden-independent",
                    "holds": True,
                    "violated": False,
                },
            ],
            "wrong_provenance_criteria",
        ),
    ],
)
def test_contract_grade_cannot_pass_partial_extra_duplicate_or_wrong_provenance(
    verdicts, diagnostic
):
    grade = grade_run(
        verdicts,
        execution_status="completed",
        expected_criterion_ids=("criterion-a", "criterion-b"),
    )

    assert grade["status"] == "inconclusive"
    assert grade["functional_pass"] is None
    assert grade["strict_pass"] is None
    assert grade[diagnostic]


def test_contract_grade_requires_unique_expected_criterion_ids():
    with pytest.raises(ValueError, match="expected criterion IDs"):
        grade_run(
            [],
            execution_status="completed",
            expected_criterion_ids=("criterion-a", "criterion-a"),
        )


def test_summary_uses_every_scheduled_test_as_the_conservative_headline_denominator():
    summary = summarize_runs(
        [
            {"status": "completed", "functional_pass": True, "strict_pass": True},
            {"status": "completed", "functional_pass": False, "strict_pass": False},
            {"status": "timeout", "functional_pass": None, "strict_pass": None},
            {"status": "error", "functional_pass": None, "strict_pass": None},
            {"status": "inconclusive", "functional_pass": None, "strict_pass": None},
        ]
    )

    assert summary["scheduled"] == 5
    assert summary["scored"] == 2
    assert summary["functional_pass_rate"] == 0.2
    assert summary["strict_pass_rate"] == 0.2
    assert summary["available_case_functional_pass_rate"] == 0.5
    assert summary["available_case_strict_pass_rate"] == 0.5
    assert summary["status_counts"] == {
        "completed": 2,
        "timeout": 1,
        "error": 1,
        "inconclusive": 1,
        "missing": 0,
    }


def test_derived_case_preserves_hidden_semantics_and_projects_only_track_runtime(tmp_path):
    test = tmp_path / "test"
    (test / "checks").mkdir(parents=True)
    candidate = b'{"skill":"demo","prompt":"hidden","base_image":"skillrace/skillgen-base:0.73.1-construction"}\n'
    dockerfile = b"FROM skillrace/skillgen-base:0.73.1-construction\nRUN true\n"
    check = b"#!/bin/sh\nexit 0\n"
    (test / "candidate.json").write_bytes(candidate)
    (test / "Dockerfile").write_bytes(dockerfile)
    (test / "checks" / "pass.sh").write_bytes(check)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Skill\n", encoding="utf-8")

    case = derive_case(
        test, "demo", skill, tmp_path / "case", agent_model="deepseek-v4-flash"
    )

    projected = json.loads((case / "candidate.json").read_text())
    assert projected["base_image"] == (
        "skillrace/skillgen-base:0.73.1-deepseek-v4-flash"
    )
    assert (case / "Dockerfile").read_text().startswith(
        "FROM skillrace/skillgen-base:0.73.1-deepseek-v4-flash\n"
    )
    assert (case / "checks" / "pass.sh").read_bytes() == check
    receipt = json.loads((case / "runtime-projection.json").read_text())
    assert receipt["model"] == "deepseek-v4-flash"
    assert receipt["source_candidate_sha256"] == file_hash(test / "candidate.json")
    assert receipt["source_dockerfile_sha256"] == file_hash(test / "Dockerfile")
    assert projected["skill"] == "demo"


def test_condition_blind_executor_request_has_no_condition_field(tmp_path):
    assert {
        "criterion_ids",
        "validation_image_digest",
    }.issubset(HiddenExecutionRequest.__dataclass_fields__)
    request = HiddenExecutionRequest(
        test_id="scenario/t1",
        hidden_case_dir=tmp_path / "hidden",
        skill_name="demo",
        skill_dir=tmp_path / "skill",
        run_dir=tmp_path / "run",
        agent_model="glm-4.5-flash",
        wall_clock=30,
        contract_identity="a" * 64,
        criterion_ids=("behavior",),
        validation_image_digest="sha256:" + "b" * 64,
    )

    assert "condition" not in request.__dataclass_fields__


def test_condition_blind_executor_reads_nested_wall_time_and_agent_cost(
    tmp_path, monkeypatch
):
    hidden = tmp_path / "hidden"
    (hidden / "checks").mkdir(parents=True)
    (hidden / "candidate.json").write_text(
        json.dumps(
            {
                "skill": "demo",
                "base_image": "skillrace/skillgen-base:0.73.1-construction",
                "prompt": "task",
            }
        ),
        encoding="utf-8",
    )
    (hidden / "Dockerfile").write_text(
        "FROM skillrace/skillgen-base:0.73.1-construction\n", encoding="utf-8"
    )
    (hidden / "checks" / "pass.sh").write_text("exit 0\n", encoding="utf-8")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Skill\n", encoding="utf-8")

    class Completed:
        returncode = 0

    def fake_invoke(_case, execution_dir, *_args):
        execution_dir.mkdir(parents=True)
        (execution_dir / "cost.json").write_text(
            json.dumps(
                {
                    "in": 10,
                    "out": 3,
                    "cost_provider_credits": 0.251234,
                    "price_provider_credits": 0.25,
                }
            ),
            encoding="utf-8",
        )
        (execution_dir / "launch.json").write_text(
            json.dumps({"schema": "skillrace-hidden-launch/1"}), encoding="utf-8"
        )
        (execution_dir / "run.json").write_text(
            json.dumps(
                {
                    "run_id": "run-1",
                    "termination": {"reason": "completed", "seconds": 12.5},
                }
            ),
            encoding="utf-8",
        )
        verdicts = [
            {
                "property_id": "behavior",
                "provenance": "hidden-independent",
                "holds": True,
                "violated": False,
            }
        ]
        (execution_dir / "verdicts.json").write_text(
            json.dumps(verdicts), encoding="utf-8"
        )
        return (
            Completed(),
            Completed(),
            verdicts,
            {"run_id": "run-1", "termination": {"reason": "completed", "seconds": 12.5}},
        )

    monkeypatch.setattr(skill_eval_module, "_invoke_shared_runner", fake_invoke)
    result = execute_hidden_request(
        HiddenExecutionRequest(
            test_id="scenario/t1",
            hidden_case_dir=hidden,
            skill_name="demo",
            skill_dir=skill,
            run_dir=tmp_path / "run",
            agent_model="glm-4.5-flash",
            wall_clock=30,
            contract_identity="a" * 64,
            criterion_ids=("behavior",),
            validation_image_digest="sha256:" + "b" * 64,
        )
    )

    assert result["wall_seconds"] == 12.5
    assert result["cost_provider_credits"] == 0.251234
    assert result["raw_artifacts"] == {
        name: {
            "path": f"execution/{filename}",
            "sha256": file_hash(tmp_path / "run" / "execution" / filename),
        }
        for name, filename in {
            "launch": "launch.json",
            "run": "run.json",
            "verdicts": "verdicts.json",
            "cost": "cost.json",
        }.items()
    }


def test_shared_hidden_launch_records_clean_cwd_argv_env_and_oracle_provenance(
    tmp_path, monkeypatch
):
    case = tmp_path / "public-copy" / "case"
    checks = case / "checks"
    checks.mkdir(parents=True)
    skill = tmp_path / "public-copy" / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Skill\n")
    run_dir = tmp_path / "evaluation" / "execution"
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        run_dir.mkdir(parents=True, exist_ok=True)
        if "skillrace.run_case" in argv:
            (run_dir / "run.json").write_text(
                json.dumps({"run_id": "agent-1", "termination": {"reason": "completed"}})
            )
        else:
            (run_dir / "verdicts.json").write_text("[]\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(skill_eval_module.subprocess, "run", fake_run)
    monkeypatch.setenv("yunwu_key", "not-recorded")

    skill_eval_module._invoke_shared_runner(
        case, run_dir, "glm-4.5-flash", 30, checks, skill
    )

    launch = json.loads((run_dir / "launch.json").read_text())
    assert launch["schema"] == "skillrace-hidden-launch/1"
    assert all(pathlib.Path(entry["cwd"]) == run_dir.resolve() for entry in launch["commands"])
    assert all("condition" not in " ".join(entry["argv"]) for entry in launch["commands"])
    checker_argv = launch["commands"][1]["argv"]
    assert checker_argv[-2:] == ["--verdict-provenance", "hidden-independent"]
    assert launch["environment"]["values_recorded"] is False
    assert launch["environment"]["secret_names"] == ["yunwu_key"]
    assert all(kwargs["cwd"] == run_dir for _, kwargs in calls)
    assert all(set(kwargs["env"]) == set(launch["environment"]["names"]) for _, kwargs in calls)


def test_raw_execution_artifact_inventory_rejects_symlinked_root(tmp_path):
    real = tmp_path / "real-execution"
    real.mkdir()
    for filename in ("launch.json", "run.json", "verdicts.json", "cost.json"):
        (real / filename).write_text("{}\n", encoding="utf-8")
    linked = tmp_path / "linked-execution"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        raw_execution_artifacts(linked)
