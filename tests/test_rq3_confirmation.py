from __future__ import annotations

import hashlib
import json

import pytest

from skillrace.rq3 import UncertainExternalOutcomeError
from skillrace.rq3_confirmation import (
    ConfirmationRequest,
    confirm_campaign_findings,
    failure_signature,
    validate_confirmation_ledger,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _campaign() -> dict:
    attempts = []
    for ordinal, (candidate, detail) in enumerate(
        (("c1", "expected 2, got 3"), ("c2", "expected 2, got 3"), ("c3", "crashed with TypeError"))
    ):
        verdict = {
            "property_id": "behavior",
            "holds": False,
            "violated": True,
            "detail": detail,
        }
        attempts.append(
            {
                "execution_id": f"e{ordinal:04d}",
                "attempt_id": f"e{ordinal:04d}-a00",
                "consume_budget": True,
                "candidate_id": candidate,
                "case": f"cases/{candidate}",
                "provenance": {
                    "task_nl": f"task for {candidate}",
                    "env_nl": f"environment for {candidate}",
                },
                "violated": ["behavior"],
                "result": {
                    "verdicts": [verdict],
                    "failure_signatures": {
                        "behavior": failure_signature(verdict)
                    },
                },
            }
        )
    return {
        "schema": "campaign/2",
        "method": "skillrace",
        "protocol_hash": _digest("protocol"),
        "base_skill_hash": _digest("base"),
        "budget": 30,
        "counted_executions": 30,
        "complete": True,
        "attempts": attempts,
    }


def test_confirmation_deduplicates_property_signature_and_runs_each_representative_once(
    tmp_path,
):
    campaign = _campaign()
    calls: list[ConfirmationRequest] = []

    def executor(request: ConfirmationRequest):
        calls.append(request)
        detail = (
            "expected 2, got 3"
            if request.representative_candidate_id == "c1"
            else "crashed with TypeError"
        )
        return {
            "status": "completed",
            "verdicts": [
                {
                    "property_id": "behavior",
                    "holds": False,
                    "violated": True,
                    "detail": detail,
                }
            ],
            "agent_id": f"confirm-{len(calls)}",
            "cost_usd": 0.03,
            "input_tokens": 8,
            "output_tokens": 2,
        }

    ledger = confirm_campaign_findings(
        campaign,
        tmp_path / "confirmations",
        executor=executor,
    )

    assert len(calls) == 2
    assert [call.representative_candidate_id for call in calls] == ["c1", "c3"]
    assert ledger["search_agent_executions"] == 30
    assert ledger["confirmation_executions"] == 2
    assert ledger["confirmation_executions_counted_in_search_budget"] is False
    assert ledger["costs"]["total_usd"] == pytest.approx(0.06)
    assert all(cluster["status"] == "confirmed" for cluster in ledger["clusters"])
    assert ledger["clusters"][0]["task_summary"] == "task for c1"
    assert ledger["clusters"][0]["environment_summary"] == "environment for c1"
    validate_confirmation_ledger(tmp_path / "confirmations" / "confirmation.json")

    calls.clear()
    resumed = confirm_campaign_findings(
        campaign,
        tmp_path / "confirmations",
        executor=lambda _request: pytest.fail("confirmed reruns must not repeat"),
    )
    assert calls == []
    assert resumed == ledger


def test_confirmation_unknown_external_outcome_is_not_retried(tmp_path):
    campaign = _campaign()

    class ProcessLost(BaseException):
        pass

    with pytest.raises(ProcessLost):
        confirm_campaign_findings(
            campaign,
            tmp_path / "confirmations",
            executor=lambda _request: (_ for _ in ()).throw(ProcessLost()),
        )

    starts = sorted((tmp_path / "confirmations" / "clusters").glob("*/start.json"))
    assert len(starts) == 1
    with pytest.raises(UncertainExternalOutcomeError, match="confirmation outcome is unknown"):
        confirm_campaign_findings(
            campaign,
            tmp_path / "confirmations",
            executor=lambda _request: pytest.fail("unknown rerun must not repeat"),
        )


def test_confirmation_validator_rejects_tampered_result(tmp_path):
    campaign = _campaign()

    def executor(request):
        return {
            "status": "completed",
            "verdicts": [
                {
                    "property_id": request.property_id,
                    "holds": False,
                    "violated": True,
                    "detail": request.failure_summary,
                }
            ],
            "cost_usd": 0.0,
        }

    ledger = confirm_campaign_findings(
        campaign, tmp_path / "confirmations", executor=executor
    )
    first = ledger["clusters"][0]["cluster_id"]
    result = tmp_path / "confirmations" / "clusters" / first / "result.json"
    value = json.loads(result.read_text())
    value["status"] = "error"
    result.write_text(json.dumps(value))

    with pytest.raises(ValueError, match="result hash"):
        validate_confirmation_ledger(tmp_path / "confirmations" / "confirmation.json")


def test_confirmation_derives_failure_signature_from_immutable_run_verdict_receipt(
    tmp_path,
):
    campaign = _campaign()
    first = campaign["attempts"][0]
    first["result"].pop("failure_signatures")
    first["result"].pop("verdicts")
    run = tmp_path / "campaign" / "runs" / "e0000"
    run.mkdir(parents=True)
    (run / "verdicts.json").write_text(
        json.dumps(
            [
                {
                    "property_id": "behavior",
                    "violated": True,
                    "holds": False,
                    "detail": "expected 2, got 3",
                }
            ]
        )
    )
    first["run"] = "runs/e0000"

    ledger = confirm_campaign_findings(
        campaign,
        tmp_path / "confirmations",
        campaign_root=tmp_path / "campaign",
        executor=lambda request: {
            "status": "completed",
            "verdicts": [
                {
                    "property_id": request.property_id,
                    "violated": True,
                    "holds": False,
                    "detail": request.failure_summary,
                }
            ],
            "cost_usd": 0.0,
        },
    )

    assert ledger["clusters"][0]["representative_attempt_id"] == "e0000-a00"
