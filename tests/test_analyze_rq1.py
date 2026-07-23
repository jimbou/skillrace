from __future__ import annotations

import csv
import json

import pytest

import skillrace.analyze_rq1 as analyze_module
from skillrace.analyze_rq1 import (
    RQ1AnalysisError,
    analyze_verified_cells,
    discovery_curve,
    normalized_auc,
    survival_record,
    verify_rq1_cell,
    verify_rq1_experiment,
    write_analysis_outputs,
)
from skillrace.io_utils import canonical_json_hash
from skillrace.repair_validation import (
    RQ1_REPAIR_EVIDENCE_MAX_BYTES,
    repair_campaign_failures,
)
from skillrace.rq3_confirmation import confirm_campaign_findings, failure_signature


def test_discovery_curve_counts_distinct_confirmed_clusters():
    events = [(1, "d1"), (2, "d1"), (4, "d2")]
    assert discovery_curve(events, budget=5) == [1, 1, 1, 2, 2]


def test_auc_is_normalized_by_budget_and_maximum_observed_yield():
    assert normalized_auc([0, 1, 1, 2]) == 0.5
    assert normalized_auc([0, 0, 0], maximum_final_yield=0) == 0.0


def test_survival_record_is_one_based_and_right_censored():
    assert survival_record([False, True, False]) == {"time": 2, "observed": True}
    assert survival_record([False, False, False]) == {"time": 3, "observed": False}


def test_rq1_path_resolver_accepts_contained_legacy_workspace_relative_path(
    tmp_path, monkeypatch
):
    root = tmp_path / "cell"
    target = root / "runs" / "saved"
    target.mkdir(parents=True)
    monkeypatch.chdir(tmp_path.parent)
    legacy = f"{tmp_path.name}/cell/runs/saved"

    assert analyze_module._within(root.resolve(), legacy, "run path") == target.resolve()


def _cell(method, skill, family, confirmed, repairs):
    return {
        "method": method,
        "skill": skill,
        "family": family,
        "contingency": "high",
        "budget": 30,
        "confirmed_events": [
            {"execution": index + 1, "cluster_id": f"{skill}-{method}-d{index + 1}"}
            for index in range(confirmed)
        ],
        "raw_failed_executions": len(repairs),
        "repair_statuses": list(repairs),
        "confirmation_executions": confirmed,
        "confirmation_statuses": {"confirmed": confirmed},
        "classifications": {},
        "targeting": {},
        "costs": {
            "search_provider_credits": 1.0,
            "confirmation_provider_credits": 0.1,
            "repair_provider_credits": 0.2,
            "inclusive_provider_credits": 1.3,
        },
    }


def _cells():
    values = {
        "s1": ("cli", {"random": 1, "greybox": 0, "skillrace": 2}),
        "s2": ("cli", {"random": 0, "greybox": 1, "skillrace": 1}),
        "s3": ("sql", {"random": 1, "greybox": 1, "skillrace": 3}),
        "s4": ("sql", {"random": 2, "greybox": 0, "skillrace": 1}),
    }
    rows = []
    for skill, (family, methods) in values.items():
        for method, count in methods.items():
            repairs = ["repaired"] * count + ["same_failure"]
            rows.append(_cell(method, skill, family, count, repairs))
    return rows


def test_analysis_reports_confirmed_yield_repair_denominator_and_family_pairs():
    result = analyze_verified_cells(
        _cells(),
        bootstrap_samples=2000,
        bootstrap_seed=20260712,
    )

    assert result["schema"] == "skillrace-rq1-analysis/1"
    skillrace = result["by_method"]["skillrace"]
    assert skillrace["confirmed_distinct_defects"] == 7
    assert skillrace["search_agent_executions"] == 120
    assert skillrace["confirmed_defect_yield"] == pytest.approx(7 / 120)
    assert skillrace["repair_executions"] == 11
    assert skillrace["repaired"] == 7
    assert skillrace["repair_rate"] == pytest.approx(7 / 11)
    contrast = result["paired_contrasts"]["skillrace_minus_random"]
    assert contrast["estimate"] == pytest.approx(0.025)
    assert contrast["family_count"] == 2
    assert contrast["skill_count"] == 4
    assert contrast["ci95"][0] <= contrast["estimate"] <= contrast["ci95"][1]
    assert result["per_cell"][0]["discovery_curve"]
    assert all("normalized_auc" in row for row in result["per_cell"])


def test_analysis_rejects_missing_or_duplicate_method_skill_cells():
    cells = _cells()
    with pytest.raises(RQ1AnalysisError, match="duplicate"):
        analyze_verified_cells(cells + [dict(cells[0])], bootstrap_samples=20)
    with pytest.raises(RQ1AnalysisError, match="paired|exactly"):
        analyze_verified_cells(cells[:-1], bootstrap_samples=20)


def test_machine_owned_outputs_are_deterministic_and_include_plot_source(tmp_path):
    result = analyze_verified_cells(
        _cells(), bootstrap_samples=200, bootstrap_seed=7
    )
    paths = write_analysis_outputs(result, tmp_path)
    first = {name: path.read_bytes() for name, path in paths.items()}
    paths = write_analysis_outputs(result, tmp_path)

    assert {name: path.read_bytes() for name, path in paths.items()} == first
    assert set(paths) == {"json", "csv", "latex", "plot_csv"}
    assert json.loads(paths["json"].read_text())["schema"] == result["schema"]
    with paths["plot_csv"].open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 4 * 3 * 30
    assert "SkillRACEConfirmedYield" in paths["latex"].read_text()


def _raw_cell_artifacts(
    tmp_path, *, unrepaired_attempts=(), budget=30, omit_result_run_id=False
):
    root = tmp_path / "cell"
    original = tmp_path / "skill"
    original.mkdir()
    (original / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    protocol = {
        "schema": "campaign-protocol/1",
        "protocol_id": "test-rq1",
        "status": "frozen" if budget == 30 else "runtime",
        "model": "same-model",
        "budget": budget,
        "bootstrap_count": 10,
        "max_generation_attempts_per_execution": 5,
        "seed_generator": {"batch_size": 5, "temperature": 0.9, "build_retries": 4},
        "greybox_level": "L1",
        "random_seed": 7,
    }
    attempts = []
    for ordinal in range(budget):
        execution = f"e{ordinal:04d}"
        candidate = f"candidate-{ordinal:03d}"
        case = root / "cases" / candidate
        run = root / "runs" / candidate
        case.mkdir(parents=True)
        run.mkdir(parents=True)
        (case / "candidate.json").write_text("{}\n", encoding="utf-8")
        if ordinal < 2:
            detail = "expected 2, got 3"
        elif ordinal == 2:
            detail = "crashed with TypeError"
        else:
            detail = None
        verdicts = (
            [
                {
                    "property_id": "behavior",
                    "holds": False,
                    "violated": True,
                    "detail": detail,
                }
            ]
            if detail
            else [
                {
                    "property_id": "behavior",
                    "holds": True,
                    "violated": False,
                    "detail": "ok",
                }
            ]
        )
        (run / "verdicts.json").write_text(json.dumps(verdicts), encoding="utf-8")
        (run / "cost.json").write_text(
            json.dumps({"model": "same-model", "in": 10, "out": 2, "price_provider_credits": 0.01}),
            encoding="utf-8",
        )
        (run / "run.json").write_text(
            json.dumps(
                {
                    "run_id": f"agent-{ordinal:03d}",
                    "model": "same-model",
                    "agent_started": True,
                    "termination": {"reason": "completed", "seconds": 1.0},
                }
            ),
            encoding="utf-8",
        )
        result = {
            "agent_started": True,
            "status": "completed",
            "runner_status": "completed",
            "oracle_status": "completed",
            "violated": ["behavior"] if detail else [],
            "inconclusive": [],
            "verdicts": verdicts,
            "failure_signatures": (
                {"behavior": failure_signature(verdicts[0])} if detail else {}
            ),
            "run_id": f"agent-{ordinal:03d}",
            "run_dir": f"runs/{candidate}",
            "input_tokens": 10,
            "output_tokens": 2,
            "cost_provider_credits": 0.01,
        }
        if omit_result_run_id:
            result.pop("run_id")
        attempt = {
            "execution_id": execution,
            "attempt_id": f"{execution}-a00",
            "consume_budget": True,
            "candidate_id": candidate,
            "case": f"cases/{candidate}",
            "run": f"runs/{candidate}",
            "agent_started": True,
            "violated": list(result["violated"]),
            "provenance": {
                "source": "random",
                "independent_test": True,
                "task_nl": f"task {ordinal}",
                "env_nl": f"environment {ordinal}",
            },
            "classification": None,
            "result": result,
        }
        attempt_dir = root / "attempts" / attempt["attempt_id"]
        attempt_dir.mkdir(parents=True)
        proposal = {
            "candidate": {
                "candidate_id": candidate,
                "provenance": attempt["provenance"],
            },
            "phase": "explore",
        }
        receipt = {"candidate_id": candidate, "result": result}
        cleanup_intent = {"candidate_id": candidate, "action": "missing"}
        cleanup = {"candidate_id": candidate, "status": "missing"}
        fold = {"phase": "explore", "classification": None}
        for name, value in (
            ("proposal.json", proposal),
            ("receipt.json", receipt),
            ("cleanup.intent.json", cleanup_intent),
            ("cleanup.json", cleanup),
            ("fold.json", fold),
        ):
            (attempt_dir / name).write_text(json.dumps(value), encoding="utf-8")
        attempt.update(
            {
                "phase": "explore",
                "proposal_hash": canonical_json_hash(proposal),
                "receipt_hash": canonical_json_hash(receipt),
                "cleanup_intent_hash": canonical_json_hash(cleanup_intent),
                "cleanup_hash": canonical_json_hash(cleanup),
                "fold_hash": canonical_json_hash(fold),
            }
        )
        attempts.append(attempt)
    campaign = {
        "schema": "campaign/2",
        "protocol_id": "test-rq1",
        "protocol_hash": canonical_json_hash(protocol),
        "protocol": protocol,
        "method": "random",
        "skill": "demo",
        "budget": budget,
        "counted_executions": budget,
        "allocation": {"budget": budget, "bootstrap": 0, "exploration": budget},
        "model": "same-model",
        "agent_model": "same-model",
        "complete": True,
        "status": "completed",
        "attempts": attempts,
        "iterations": [dict(attempt) for attempt in attempts],
        "generator_state": {"gen_cost_provider_credits": 0.0},
        "bootstrap_generator_state": {},
    }
    root.mkdir(parents=True, exist_ok=True)
    campaign_path = root / "campaign.json"
    campaign_path.write_text(json.dumps(campaign), encoding="utf-8")
    confirmations = confirm_campaign_findings(
        campaign,
        root / "confirmations",
        campaign_root=root,
        allow_bounded_development=budget < 30,
        executor=lambda request: {
            "status": "completed",
            "verdicts": [
                {
                    "property_id": request.property_id,
                    "holds": False,
                    "violated": True,
                    "detail": request.failure_summary,
                }
            ],
            "agent_id": f"confirm-{request.cluster_id}",
            "input_tokens": 4,
            "output_tokens": 1,
            "cost_provider_credits": 0.02,
        },
    )

    def patcher(request, _evidence, patch_root):
        skill = patch_root / "skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Patched\n", encoding="utf-8")
        return {
            "status": "completed",
            "skill_dir": str(skill),
            "operation_id": f"patch.{request.repair_id}",
            "input_tokens": 5,
            "output_tokens": 2,
            "cost_provider_credits": 0.01,
        }

    def replay(request, *_):
        if request.attempt_id in set(unrepaired_attempts):
            return {
                "status": "completed",
                "verdicts": [
                    {
                        "property_id": property_id,
                        "holds": False,
                        "violated": True,
                        "detail": "expected 2, got 3",
                    }
                    for property_id in request.failed_property_ids
                ],
                "cost_provider_credits": 0.03,
            }
        return {
            "status": "completed",
            "verdicts": [
                {
                    "property_id": property_id,
                    "holds": True,
                    "violated": False,
                }
                for property_id in request.failed_property_ids
            ],
            "cost_provider_credits": 0.03,
        }

    repairs = repair_campaign_failures(
        campaign,
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=root,
        output_root=root / "repairs",
        patcher=patcher,
        executor=replay,
        evidence_max_bytes=3600,
    )
    return root, original, campaign_path, confirmations, repairs


def test_bounded_development_cell_uses_rq1_counting_without_weakening_default(tmp_path):
    root, original, campaign_path, _, _ = _raw_cell_artifacts(
        tmp_path, budget=2, omit_result_run_id=True
    )
    arguments = {
        "campaign_path": campaign_path,
        "confirmation_path": root / "confirmations" / "confirmation.json",
        "repair_path": root / "repairs" / "repairs.json",
        "original_skill_dir": original,
        "expected_method": "random",
        "expected_skill": "demo",
        "family": "development",
        "contingency": "development",
    }

    with pytest.raises(RQ1AnalysisError, match="30-run"):
        verify_rq1_cell(**arguments)

    row = verify_rq1_cell(**arguments, allow_bounded_development=True)

    assert row["budget"] == 2
    assert row["candidate_accounting"]["counted"] == 2
    assert row["repair_statuses"] == ["repaired", "repaired"]


def test_strict_cell_verifier_joins_confirmation_and_every_raw_failure_repair(tmp_path):
    root, original, campaign_path, _, _ = _raw_cell_artifacts(tmp_path)
    row = verify_rq1_cell(
        campaign_path=campaign_path,
        confirmation_path=root / "confirmations" / "confirmation.json",
        repair_path=root / "repairs" / "repairs.json",
        original_skill_dir=original,
        expected_method="random",
        expected_skill="demo",
        family="cli",
        contingency="high",
    )

    assert row["confirmed_events"] == [
        {"execution": 1, "cluster_id": row["confirmed_events"][0]["cluster_id"]},
        {"execution": 3, "cluster_id": row["confirmed_events"][1]["cluster_id"]},
    ]
    assert row["raw_failed_executions"] == 3
    assert row["repair_statuses"] == ["repaired", "repaired", "repaired"]
    assert row["confirmation_executions"] == 2
    assert row["runs_with_violation"] == 3


def test_strict_cell_verifier_accepts_patch_only_plus_separate_confirmation(tmp_path):
    root, original, campaign_path, _, historical = _raw_cell_artifacts(tmp_path)
    patches = {
        "schema": "skillrace-patch-only-ledger/1",
        "method": "random",
        "skill_name": "demo",
        "source_campaign_hash": historical["source_campaign_hash"],
        "original_skill_hash": historical["original_skill_hash"],
        "failed_public_executions": 3,
        "patch_executions": 3,
        "patches": [
            {
                "repair_id": link["repair_id"],
                "execution_id": link["execution_id"],
                "attempt_id": link["attempt_id"],
                "status": "completed",
                "backend": "direct",
            }
            for link in historical["repairs"]
        ],
        "cost_provider_credits": 0.03,
    }
    patch_path = root / "patch-only" / "patches.json"
    patch_path.parent.mkdir()
    patch_path.write_text(json.dumps(patches), encoding="utf-8")
    confirmations = {
        "schema": "skillrace-patch-confirmations/1",
        "method": "random",
        "source_campaign_hash": historical["source_campaign_hash"],
        "patch_ledger_hash": canonical_json_hash(patches),
        "failed_public_executions": 3,
        "confirmation_executions": 3,
        "confirmed_defects": 3,
        "confirmations": [
            {
                "repair_id": link["repair_id"],
                "execution_id": link["execution_id"],
                "attempt_id": link["attempt_id"],
                "status": "repair_confirmed",
            }
            for link in historical["repairs"]
        ],
        "cost_provider_credits": 0.09,
    }
    confirmation_path = root / "patch-only-confirmations" / "confirmations.json"
    confirmation_path.parent.mkdir()
    confirmation_path.write_text(json.dumps(confirmations), encoding="utf-8")

    row = verify_rq1_cell(
        campaign_path=campaign_path,
        confirmation_path=root / "confirmations" / "confirmation.json",
        repair_path=patch_path,
        patch_confirmation_path=confirmation_path,
        original_skill_dir=original,
        expected_method="random",
        expected_skill="demo",
        family="cli",
        contingency="high",
    )
    assert row["repair_statuses"] == ["repair_confirmed"] * 3
    assert len(row["confirmed_events"]) == 2
    assert row["costs"]["repair_provider_credits"] == pytest.approx(0.12)
    assert row["oracle_statuses"] == {"completed": 30}
    assert row["candidate_accounting"]["counted"] == 30
    assert row["candidate_accounting"]["pre_agent_rejected"] == 0
    assert row["costs"]["search_provider_credits"] == pytest.approx(0.3)
    assert row["costs"]["confirmation_provider_credits"] == pytest.approx(0.04)
    assert row["costs"]["repair_provider_credits"] == pytest.approx(0.12)
    assert row["costs"]["inclusive_provider_credits"] == pytest.approx(0.46)


def test_headline_defect_requires_reproduction_and_exact_case_repair(tmp_path):
    root, original, campaign_path, _, _ = _raw_cell_artifacts(
        tmp_path, unrepaired_attempts={"e0000-a00"}
    )
    row = verify_rq1_cell(
        campaign_path=campaign_path,
        confirmation_path=root / "confirmations" / "confirmation.json",
        repair_path=root / "repairs" / "repairs.json",
        original_skill_dir=original,
        expected_method="random",
        expected_skill="demo",
        family="cli",
        contingency="high",
    )

    assert [event["execution"] for event in row["reproduced_events"]] == [1, 3]
    assert [event["execution"] for event in row["confirmed_events"]] == [3]
    assert row["repair_validation_statuses"] == {
        "repair-validated": 1,
        "reproduced-but-not-repaired": 1,
    }


def test_strict_cell_verifier_rejects_missing_repair_even_if_ledger_is_rewritten(tmp_path):
    root, original, campaign_path, _, _ = _raw_cell_artifacts(tmp_path)
    ledger_path = root / "repairs" / "repairs.json"
    ledger = json.loads(ledger_path.read_text())
    ledger["repairs"].pop()
    ledger["failed_public_executions"] = 2
    ledger["repair_executions"] = 2
    ledger["costs"] = {"patch_provider_credits": 0.02, "replay_provider_credits": 0.06, "total_provider_credits": 0.08}
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    with pytest.raises(RQ1AnalysisError, match="one repair|failed public"):
        verify_rq1_cell(
            campaign_path=campaign_path,
            confirmation_path=root / "confirmations" / "confirmation.json",
            repair_path=ledger_path,
            original_skill_dir=original,
            expected_method="random",
            expected_skill="demo",
            family="cli",
            contingency="high",
        )


def test_strict_cell_verifier_rejects_tampered_campaign_attempt_receipt(tmp_path):
    root, original, campaign_path, _, _ = _raw_cell_artifacts(tmp_path)
    receipt = root / "attempts" / "e0000-a00" / "receipt.json"
    value = json.loads(receipt.read_text())
    value["result"]["status"] = "timeout"
    receipt.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(RQ1AnalysisError, match="receipt.*hash"):
        verify_rq1_cell(
            campaign_path=campaign_path,
            confirmation_path=root / "confirmations" / "confirmation.json",
            repair_path=root / "repairs" / "repairs.json",
            original_skill_dir=original,
            expected_method="random",
            expected_skill="demo",
            family="cli",
            contingency="high",
        )


def test_experiment_loader_requires_exact_paired_cells_and_enabled_postsearch_phases(
    tmp_path, monkeypatch
):
    skill = tmp_path / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    d1_path = tmp_path / "d1.json"
    d1_path.write_text(
        json.dumps(
            {
                "schema": "d1-suite/1",
                "suite_id": "fixture",
                "status": "draft",
                "selection_rule": "x" * 50,
                "headline_skills": [
                    {
                        "id": "demo",
                        "family": "cli",
                        "contingency": "high",
                        "base_image": "skillrace/demo:base",
                    }
                ],
                "excluded_public": [],
                "development_only": [],
            }
        )
    )
    manifest = {
        "schema": "skillrace-experiment-manifest/1",
        "campaign_workers": 3,
        "resources": {"api": 1, "docker": 1, "agent": 1},
        "confirmation": {"enabled": True},
        "repair": {
            "enabled": True,
            "evidence_max_bytes": RQ1_REPAIR_EVIDENCE_MAX_BYTES,
        },
        "cells": [
            {
                "id": f"{method}-demo",
                "output": f"cells/{method}-demo",
                "campaign": {
                    "method": method,
                    "skill": "demo",
                    "skill_dir": str(skill),
                },
            }
            for method in ("random", "greybox", "skillrace")
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    output = tmp_path / "output"
    schedule = {
        "schema": "skillrace-experiment-schedule/1",
        "manifest_hash": canonical_json_hash(manifest),
        "status": "completed",
        "confirmation": {"enabled": True},
        "repair": {
            "enabled": True,
            "evidence_max_bytes": RQ1_REPAIR_EVIDENCE_MAX_BYTES,
        },
        "cells": [
            {
                "id": cell["id"],
                "output": str((output / cell["output"]).resolve()),
                "status": "completed",
                "result": {},
                "error": None,
            }
            for cell in manifest["cells"]
        ],
    }
    schedule_path = output / "schedule.json"
    schedule_path.parent.mkdir(parents=True)
    schedule_path.write_text(json.dumps(schedule))
    calls = []

    def verify(**kwargs):
        calls.append(kwargs)
        return _cell(
            kwargs["expected_method"],
            kwargs["expected_skill"],
            kwargs["family"],
            0,
            [],
        )

    monkeypatch.setattr(analyze_module, "verify_rq1_cell", verify)
    cells = verify_rq1_experiment(
        experiment_manifest_path=manifest_path,
        schedule_path=schedule_path,
        d1_manifest_path=d1_path,
        require_frozen=False,
    )

    assert len(cells) == len(calls) == 3
    assert {cell["method"] for cell in cells} == {"random", "greybox", "skillrace"}
    assert all(call["original_skill_dir"] == skill.resolve() for call in calls)

    manifest["confirmation"] = {"enabled": False}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(RQ1AnalysisError, match="confirmation.*repair|post-search"):
        verify_rq1_experiment(
            experiment_manifest_path=manifest_path,
            schedule_path=schedule_path,
            d1_manifest_path=d1_path,
            require_frozen=False,
        )
