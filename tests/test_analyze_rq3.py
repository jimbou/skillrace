from __future__ import annotations

import pytest

import json

import skillrace.analyze_rq3 as analyze_module
from skillrace.analyze_rq3 import AnalysisError, analyze_rq3, records_from_rq3_manifest
from skillrace.io_utils import file_hash
from skillrace.rq3 import EVALUATION_CONDITIONS


def _row(scenario, test, condition, passed, *, strict=None, status="completed", cost=0.1):
    return {
        "scenario_id": scenario,
        "replication": 1,
        "test_id": f"{scenario}/{test}",
        "condition": condition,
        "status": status,
        "functional_pass": passed,
        "strict_pass": passed if strict is None else strict,
        "input_tokens": 10,
        "output_tokens": 2,
        "cost_usd": cost,
        "wall_seconds": 1.0,
        "search_cost_usd": 0.0,
        "confirmation_cost_usd": 0.0,
        "feedback_production_cost_usd": 0.0,
        "revision_cost_usd": 0.0,
    }


def _records():
    rows = []
    values = {
        "scenario-a": {
            "t1": {
                "zero-shot": False,
                "random-feedback": True,
                "greybox-feedback": False,
                "skillrace-feedback": True,
            },
            "t2": {
                "zero-shot": True,
                "random-feedback": True,
                "greybox-feedback": True,
                "skillrace-feedback": True,
            },
        },
        "scenario-b": {
            "t1": {
                "zero-shot": False,
                "random-feedback": False,
                "greybox-feedback": True,
                "skillrace-feedback": False,
            },
            "t2": {
                "zero-shot": False,
                "random-feedback": True,
                "greybox-feedback": True,
                "skillrace-feedback": None,
            },
        },
    }
    for scenario, tests in values.items():
        for number in range(1, 11):
            test = f"t{number}"
            conditions = tests["t1" if number % 2 else "t2"]
            for condition, passed in conditions.items():
                if passed is None:
                    rows.append(
                        _row(
                            scenario,
                            test,
                            condition,
                            None,
                            strict=None,
                            status="error",
                        )
                    )
                else:
                    rows.append(_row(scenario, test, condition, passed))
    return rows


def test_analysis_uses_zero_shot_changes_as_primary_and_baselines_as_secondary():
    result = analyze_rq3(_records())

    assert result["schema"] == "skillrace-rq3-analysis/1"
    assert result["headline"]["unit"] == "scenario"
    assert result["headline"]["one_execution_per_hidden_test"] is True
    primary = result["headline"]["primary_zero_shot_change"]
    assert tuple(primary) == (
        "random-feedback",
        "greybox-feedback",
        "skillrace-feedback",
    )
    assert primary["skillrace-feedback"]["functional_difference"] == pytest.approx(0.25)
    secondary = result["headline"]["secondary_method_contrasts"]
    assert secondary["skillrace_vs_greybox"]["functional_difference"] == pytest.approx(-0.25)
    assert secondary["skillrace_vs_random"]["functional_difference"] == pytest.approx(-0.25)
    assert secondary["skillrace_vs_greybox"]["scenario_count"] == 2
    assert secondary["skillrace_vs_greybox"]["paired_test_count"] == 20


def test_zero_shot_strict_cost_and_failures_are_reported_separately():
    result = analyze_rq3(_records())

    assert result["headline"]["primary_zero_shot_change"]["skillrace-feedback"][
        "functional_difference"
    ] == pytest.approx(0.25)
    assert "strict_difference" in result["headline"]["secondary_method_contrasts"][
        "skillrace_vs_greybox"
    ]
    skillrace = result["condition_summaries"]["skillrace-feedback"]
    assert skillrace["scheduled"] == 20
    assert skillrace["scored"] == 15
    assert skillrace["functional_pass_rate"] == pytest.approx(0.5)
    assert skillrace["available_case_functional_pass_rate"] == pytest.approx(2 / 3)
    assert skillrace["status_counts"]["error"] == 5
    assert skillrace["cost_usd"] == pytest.approx(2.0)
    assert skillrace["search_cost_usd"] == 0.0
    assert skillrace["confirmation_cost_usd"] == 0.0
    assert skillrace["inclusive_total_cost_usd"] == pytest.approx(2.0)
    assert len(result["scenario_effects"]) == 2
    assert "p_value" not in repr(result)


def test_analysis_rejects_duplicate_execution_and_nonheadline_condition():
    rows = _records()
    with pytest.raises(AnalysisError, match="duplicate"):
        analyze_rq3(rows + [dict(rows[0])])

    bad = _records()
    bad[0]["condition"] = "expert"
    with pytest.raises(AnalysisError, match="condition"):
        analyze_rq3(bad)


def test_analysis_requires_explicit_exact_four_by_ten_schedule():
    rows = [
        _row("scenario-a", "t1", condition, False)
        for condition in EVALUATION_CONDITIONS
    ]

    with pytest.raises(AnalysisError, match="t1.*t10|schedule"):
        analyze_rq3(rows)


def test_analysis_counts_explicit_missing_cells_as_nonpassing_not_dropped():
    rows = []
    for number in range(1, 11):
        for condition in EVALUATION_CONDITIONS:
            if condition == "skillrace-feedback":
                rows.append(
                    _row(
                        "scenario-a",
                        f"t{number}",
                        condition,
                        None,
                        strict=None,
                        status="missing",
                        cost=0.0,
                    )
                )
            else:
                rows.append(_row("scenario-a", f"t{number}", condition, True))

    result = analyze_rq3(rows)

    skillrace = result["condition_summaries"]["skillrace-feedback"]
    assert skillrace["scheduled"] == 10
    assert skillrace["recorded"] == 10
    assert skillrace["status_counts"]["missing"] == 10
    assert skillrace["functional_pass_rate"] == 0.0
    contrast = result["headline"]["secondary_method_contrasts"]["skillrace_vs_random"]
    assert contrast["paired_test_count"] == 10
    assert contrast["functional_difference"] == -1.0


def test_manifest_loader_verifies_results_and_emits_explicit_missing(tmp_path, monkeypatch):
    evaluations = {}
    for condition in EVALUATION_CONDITIONS:
        evaluations[condition] = {
            "skill_hash": "a" * 64,
            "tests": {
                f"scenario/t{number}": {
                    "status": "pending",
                    "result_hash": None,
                    "execution_count": 0,
                }
                for number in range(1, 11)
            },
        }
    manifest = {
        "scenario_id": "scenario",
        "replication": 1,
        "campaigns": {
            "random": {"cost_usd": 1.0},
            "greybox": {"cost_usd": 2.0},
            "skillrace": {"cost_usd": 3.0},
        },
        "revisions": {
            "random": {"cost_usd": 0.1},
            "greybox": {"cost_usd": 0.2},
            "skillrace": {"cost_usd": 0.3},
        },
        "feedback_envelopes": {
            "random": {"confirmation_cost_usd": 0.05},
            "greybox": {"confirmation_cost_usd": 0.06},
            "skillrace": {"confirmation_cost_usd": 0.07},
        },
        "evaluations": evaluations,
    }
    for condition in EVALUATION_CONDITIONS[:-1]:
        for number in range(1, 11):
            test_id = f"scenario/t{number}"
            path = tmp_path / "evaluations" / condition / "runs" / f"t{number}" / "result.json"
            path.parent.mkdir(parents=True)
            result = {
                "schema": "skillrace-hidden-result/1",
                "test_id": test_id,
                "status": "completed",
                "grade": {"functional_pass": True, "strict_pass": condition != "zero-shot"},
                "input_tokens": 10,
                "output_tokens": 2,
                "cost_usd": 0.1,
                "wall_seconds": 1.0,
            }
            path.write_text(json.dumps(result), encoding="utf-8")
            evaluations[condition]["tests"][test_id].update(
                {
                    "status": "completed",
                    "result_hash": file_hash(path),
                    "execution_count": 1,
                }
            )
    calls = []

    def verify(path, *, scenario_dir, require_complete):
        calls.append((path, scenario_dir, require_complete))
        return manifest

    monkeypatch.setattr(analyze_module, "verify_rq3_evaluation_artifacts", verify)

    scenario_dir = tmp_path / "scenarios" / "scenario"
    rows = records_from_rq3_manifest(
        tmp_path / "rq3-manifest.json", scenario_dir=scenario_dir
    )

    assert calls == [(tmp_path / "rq3-manifest.json", scenario_dir, False)]
    assert len(rows) == 40
    random_row = [row for row in rows if row["condition"] == "random-feedback"][0]
    assert random_row["feedback_production_cost_usd"] == 1.0
    assert random_row["search_cost_usd"] == 1.0
    assert random_row["confirmation_cost_usd"] == 0.05
    assert random_row["revision_cost_usd"] == 0.1
    missing = [row for row in rows if row["condition"] == "skillrace-feedback"]
    assert all(row["status"] == "missing" for row in missing)
    assert all(row["functional_pass"] is None for row in missing)
    assert analyze_rq3(rows)["condition_summaries"]["skillrace-feedback"][
        "status_counts"
    ]["missing"] == 10
    assert analyze_rq3(rows)["condition_summaries"]["random-feedback"][
        "testing_revision_evaluation_cost_usd"
    ] == pytest.approx(2.15)
    assert analyze_rq3(rows)["condition_summaries"]["random-feedback"][
        "inclusive_total_cost_usd"
    ] == pytest.approx(2.15)

    result_path = tmp_path / "evaluations" / "zero-shot" / "runs" / "t1" / "result.json"
    result_path.write_text("{}", encoding="utf-8")
    with pytest.raises(AnalysisError, match="hash"):
        records_from_rq3_manifest(
            tmp_path / "rq3-manifest.json", scenario_dir=scenario_dir
        )
