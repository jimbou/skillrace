from __future__ import annotations

import json

import pytest

from skillrace.io_utils import canonical_json_hash, file_hash
from skillrace.repair_validation import (
    FailureRepairRequest,
    UncertainRepairOutcomeError,
    repair_campaign_failures,
    repair_failed_execution,
    validate_repair_ledger,
)
from skillrace.revise_skill import package_hash
from skillrace.rq3_confirmation import confirm_campaign_findings


def _request(tmp_path) -> FailureRepairRequest:
    original = tmp_path / "original"
    original.mkdir()
    (original / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    case = tmp_path / "case"
    run = tmp_path / "run"
    case.mkdir()
    run.mkdir()
    return FailureRepairRequest(
        method="skillrace",
        skill_name="demo",
        execution_id="e0001",
        attempt_id="e0001-a00",
        candidate_id="candidate-a",
        case_dir=case,
        original_skill_dir=original,
        original_skill_hash=package_hash(original),
        failed_property_ids=("behavior",),
        failure_signatures=("b" * 64,),
        run_dir=run,
        output_dir=tmp_path / "repairs" / "repair-one",
        repair_id="repair-one",
    )


def _evidence(request: FailureRepairRequest) -> dict:
    payload = {
        "schema": "skillrace-failure-repair-evidence/1",
        "original_skill_hash": request.original_skill_hash,
        "failure_core": {
            "failures": [
                {
                    "property_id": "behavior",
                    "failure_signature": "b" * 64,
                    "mechanical_error": "wrong artifact",
                }
            ]
        },
        "method_evidence": {"reasoning_episodes": [{"intent": "repair"}]},
    }
    return {
        "schema": "skillrace-failure-repair-evidence/1",
        "repair_id": request.repair_id,
        "reviser_payload": payload,
        "evidence_hash": canonical_json_hash(payload),
        "accounting": {"max_bytes": 3600, "used_bytes": 300},
    }


def _patcher(calls, tmp_path):
    def patcher(request, evidence, patch_dir):
        calls.append((request.repair_id, evidence["evidence_hash"]))
        skill = patch_dir / "skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Repaired\n", encoding="utf-8")
        return {
            "status": "completed",
            "skill_dir": str(skill),
            "operation_id": f"patch.{request.repair_id}",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_provider_credits": 0.01,
        }

    return patcher


def test_repair_executes_patch_and_exact_case_replay_once(tmp_path):
    request = _request(tmp_path)
    evidence = _evidence(request)
    patch_calls = []
    replay_calls = []

    def executor(observed, patched_skill_dir, replay_dir):
        replay_calls.append((observed.case_dir, patched_skill_dir, replay_dir))
        return {
            "status": "completed",
            "verdicts": [
                {
                    "property_id": "behavior",
                    "holds": True,
                    "violated": False,
                    "detail": "correct artifact",
                }
            ],
            "agent_id": "repair-agent-1",
            "input_tokens": 20,
            "output_tokens": 4,
            "cost_provider_credits": 0.02,
            "wall_seconds": 3.5,
        }

    result = repair_failed_execution(
        request,
        evidence,
        patcher=_patcher(patch_calls, tmp_path),
        executor=executor,
    )

    assert result["status"] == "repaired"
    assert result["search_budget_consumed"] is False
    assert result["failed_property_ids"] == ["behavior"]
    assert result["costs"] == {
        "patch_provider_credits": 0.01,
        "replay_provider_credits": 0.02,
        "total_provider_credits": 0.03,
    }
    assert len(patch_calls) == len(replay_calls) == 1
    assert replay_calls[0][0] == request.case_dir
    assert replay_calls[0][1].name == "skill"
    assert replay_calls[0][2] == request.output_dir / "replay"

    resumed = repair_failed_execution(
        request,
        evidence,
        patcher=lambda *_: pytest.fail("terminal patch must not repeat"),
        executor=lambda *_: pytest.fail("terminal replay must not repeat"),
    )
    assert resumed == result
    assert json.loads((request.output_dir / "repair.json").read_text()) == result


def test_same_original_signature_is_classified_as_same_failure(tmp_path):
    request = _request(tmp_path)
    evidence = _evidence(request)

    result = repair_failed_execution(
        request,
        evidence,
        patcher=_patcher([], tmp_path),
        executor=lambda *_: {
            "status": "completed",
            "verdicts": [
                {
                    "property_id": "behavior",
                    "holds": False,
                    "violated": True,
                    "detail": "same failure",
                    "failure_signature": "b" * 64,
                }
            ],
        },
    )

    assert result["status"] == "same_failure"


def test_new_violation_is_classified_as_different_failure(tmp_path):
    request = _request(tmp_path)
    evidence = _evidence(request)

    result = repair_failed_execution(
        request,
        evidence,
        patcher=_patcher([], tmp_path),
        executor=lambda *_: {
            "status": "completed",
            "verdicts": [
                {
                    "property_id": "different-property",
                    "holds": False,
                    "violated": True,
                    "detail": "new failure",
                }
            ],
        },
    )

    assert result["status"] == "different_failure"


def test_crash_after_patch_intent_fails_closed_without_repatching(tmp_path):
    request = _request(tmp_path)
    evidence = _evidence(request)

    class ProcessLost(BaseException):
        pass

    with pytest.raises(ProcessLost):
        repair_failed_execution(
            request,
            evidence,
            patcher=lambda *_: (_ for _ in ()).throw(ProcessLost()),
            executor=lambda *_: pytest.fail("replay must not start"),
        )

    assert (request.output_dir / "start.json").is_file()
    assert not (request.output_dir / "patch.json").exists()
    with pytest.raises(UncertainRepairOutcomeError, match="patch outcome is unknown"):
        repair_failed_execution(
            request,
            evidence,
            patcher=lambda *_: pytest.fail("unknown patch must not repeat"),
            executor=lambda *_: pytest.fail("unknown patch must not replay"),
        )


def test_crash_after_replay_intent_fails_closed_without_rerunning_agent(tmp_path):
    request = _request(tmp_path)
    evidence = _evidence(request)

    class ProcessLost(BaseException):
        pass

    with pytest.raises(ProcessLost):
        repair_failed_execution(
            request,
            evidence,
            patcher=_patcher([], tmp_path),
            executor=lambda *_: (_ for _ in ()).throw(ProcessLost()),
        )

    assert (request.output_dir / "patch.json").is_file()
    assert (request.output_dir / "replay-start.json").is_file()
    assert not (request.output_dir / "result.json").exists()
    with pytest.raises(UncertainRepairOutcomeError, match="replay outcome is unknown"):
        repair_failed_execution(
            request,
            evidence,
            patcher=lambda *_: pytest.fail("patch must not repeat"),
            executor=lambda *_: pytest.fail("unknown replay must not repeat"),
        )


def _campaign_for_repairs(tmp_path):
    attempts = []
    for ordinal, candidate in enumerate(("candidate-a", "candidate-b", "candidate-c")):
        case = tmp_path / "campaign" / "cases" / candidate
        run = tmp_path / "campaign" / "runs" / candidate
        case.mkdir(parents=True)
        run.mkdir(parents=True)
        attempts.append(
            {
                "execution_id": f"e{ordinal:04d}",
                "attempt_id": f"e{ordinal:04d}-a00",
                "consume_budget": True,
                "candidate_id": candidate,
                "case": f"cases/{candidate}",
                "run": f"runs/{candidate}",
                "violated": ["behavior"],
                "oracle_status": "completed",
                "provenance": {
                    "task_nl": f"task {candidate}",
                    "env_nl": f"environment {candidate}",
                },
                "result": {
                    "verdicts": [
                        {
                            "property_id": "behavior",
                            "holds": False,
                            "violated": True,
                            "detail": "shared raw failure",
                        }
                    ],
                    # All three deliberately share one defect signature. Repair must
                    # still run three times because selection is per raw execution.
                    "failure_signatures": {"behavior": "c" * 64},
                },
            }
        )
    return {
        "schema": "campaign/2",
        "method": "skillrace",
        "complete": True,
        "counted_executions": 30,
        "attempts": attempts,
    }


def test_campaign_repairs_every_raw_failure_and_resumes_from_one_ledger(tmp_path):
    original = tmp_path / "original-skill"
    original.mkdir()
    (original / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    campaign = _campaign_for_repairs(tmp_path)
    patch_calls = []
    replay_calls = []

    def executor(request, _skill, _replay):
        replay_calls.append(request.execution_id)
        return {
            "status": "completed",
            "verdicts": [
                {
                    "property_id": "behavior",
                    "holds": True,
                    "violated": False,
                }
            ],
        }

    ledger = repair_campaign_failures(
        campaign,
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=tmp_path / "campaign",
        output_root=tmp_path / "repairs",
        patcher=_patcher(patch_calls, tmp_path),
        executor=executor,
        evidence_max_bytes=3600,
    )

    assert len(patch_calls) == len(replay_calls) == 3
    assert ledger["failed_public_executions"] == 3
    assert ledger["repair_executions"] == 3
    assert ledger["repair_executions_counted_in_search_budget"] is False
    assert [row["status"] for row in ledger["repairs"]] == ["repaired"] * 3
    assert len({row["repair_id"] for row in ledger["repairs"]}) == 3
    validate_repair_ledger(tmp_path / "repairs" / "repairs.json")

    resumed = repair_campaign_failures(
        campaign,
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=tmp_path / "campaign",
        output_root=tmp_path / "repairs",
        patcher=lambda *_: pytest.fail("resumed campaign must not repatch"),
        executor=lambda *_: pytest.fail("resumed campaign must not replay"),
        evidence_max_bytes=3600,
    )
    assert resumed == ledger


def test_campaign_can_delegate_each_independent_repair_to_a_confined_job(tmp_path):
    original = tmp_path / "original-skill"
    original.mkdir()
    (original / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    campaign = _campaign_for_repairs(tmp_path)
    calls = []

    def job_runner(request, evidence):
        calls.append((request.execution_id, evidence["evidence_hash"]))
        return repair_failed_execution(
            request,
            evidence,
            patcher=_patcher([], tmp_path),
            executor=lambda *_: {
                "status": "completed",
                "verdicts": [
                    {
                        "property_id": "behavior",
                        "holds": True,
                        "violated": False,
                    }
                ],
            },
        )

    ledger = repair_campaign_failures(
        campaign,
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=tmp_path / "campaign",
        output_root=tmp_path / "repairs",
        job_runner=job_runner,
        evidence_max_bytes=3600,
    )

    assert [execution for execution, _ in calls] == ["e0000", "e0001", "e0002"]
    assert ledger["repair_executions"] == 3


def test_shared_signature_gets_three_repairs_but_one_confirmation(tmp_path):
    original = tmp_path / "original-skill"
    original.mkdir()
    (original / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    campaign = _campaign_for_repairs(tmp_path)
    confirmations = []
    confirmation = confirm_campaign_findings(
        campaign,
        tmp_path / "confirmations",
        campaign_root=tmp_path / "campaign",
        executor=lambda request: confirmations.append(request) or {
            "status": "error",
            "verdicts": [],
            "cost_provider_credits": 0.0,
        },
    )
    repairs = repair_campaign_failures(
        campaign,
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=tmp_path / "campaign",
        output_root=tmp_path / "repairs",
        patcher=_patcher([], tmp_path),
        executor=lambda *_: {
            "status": "completed",
            "verdicts": [
                {"property_id": "behavior", "holds": True, "violated": False}
            ],
        },
        evidence_max_bytes=3600,
    )

    assert confirmation["confirmation_executions"] == len(confirmations) == 1
    assert repairs["repair_executions"] == 3


def test_repair_ledger_rejects_an_internally_tampered_receipt_even_if_relinked(tmp_path):
    original = tmp_path / "original-skill"
    original.mkdir()
    (original / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    campaign = _campaign_for_repairs(tmp_path)
    repair_campaign_failures(
        campaign,
        skill_name="demo",
        original_skill_dir=original,
        campaign_root=tmp_path / "campaign",
        output_root=tmp_path / "repairs",
        patcher=_patcher([], tmp_path),
        executor=lambda *_: {
            "status": "completed",
            "verdicts": [
                {"property_id": "behavior", "holds": True, "violated": False}
            ],
        },
        evidence_max_bytes=3600,
    )
    ledger_path = tmp_path / "repairs" / "repairs.json"
    ledger = json.loads(ledger_path.read_text())
    first = ledger["repairs"][0]
    receipt_path = tmp_path / "repairs" / first["repair_id"] / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["result_hash"] = "f" * 64
    receipt_path.write_text(json.dumps(receipt))
    first["receipt_file_hash"] = file_hash(receipt_path)
    ledger_path.write_text(json.dumps(ledger))

    with pytest.raises(ValueError, match="receipt|result hash"):
        validate_repair_ledger(ledger_path)
