from __future__ import annotations

import json

import pytest

from skillrace.development_gate import (
    DevelopmentGateError,
    build_development_gate_report,
    write_development_gate_report,
)
from skillrace.io_utils import canonical_json_hash


def _artifacts():
    campaign = {
        "schema": "campaign/2",
        "status": "completed",
        "complete": True,
        "method": "skillrace",
        "skill": "demo",
        "model": "deepseek-v3.2",
        "budget": 2,
        "counted_executions": 2,
        "protocol": {"status": "runtime"},
        "attempts": [
            {
                "execution_id": "e0000",
                "attempt_id": "e0000-a00",
                "consume_budget": True,
                "agent_started": True,
                "violated": ["behavior"],
                "result": {
                    "agent_started": True,
                    "verdicts": [
                        {
                            "property_id": "behavior",
                            "holds": False,
                            "violated": True,
                        }
                    ],
                },
            },
            {
                "execution_id": "e0001",
                "attempt_id": "e0001-a00",
                "consume_budget": True,
                "agent_started": True,
                "violated": [],
                "result": {"agent_started": True, "verdicts": []},
            },
        ],
    }
    source_hash = canonical_json_hash(campaign)
    repairs = {
        "schema": "skillrace-failure-repairs/1",
        "source_campaign_hash": source_hash,
        "search_agent_executions": 2,
        "failed_public_executions": 1,
        "repair_executions": 1,
        "repairs": [
            {
                "repair_id": "a" * 24,
                "execution_id": "e0000",
                "attempt_id": "e0000-a00",
                "status": "repaired",
            }
        ],
        "costs": {"total_provider_credits": 0.2},
    }
    confirmations = {
        "schema": "skillrace-confirmations/1",
        "source_campaign_hash": source_hash,
        "search_agent_executions": 2,
        "confirmation_executions": 1,
        "development_only": True,
        "clusters": [
            {
                "cluster_id": "b" * 24,
                "representative_execution_id": "e0000",
                "representative_attempt_id": "e0000-a00",
                "property_id": "behavior",
                "status": "confirmed",
            }
        ],
        "costs": {"total_provider_credits": 0.1},
    }
    return campaign, repairs, confirmations


def test_development_gate_joins_failure_repair_confirmation_and_analysis(tmp_path):
    campaign, repairs, confirmations = _artifacts()

    report = build_development_gate_report(campaign, repairs, confirmations)

    assert report["schema"] == "skillrace-bounded-development-gate/1"
    assert report["status"] == "passed"
    assert report["development_only"] is True
    assert report["search_agent_executions"] == 2
    assert report["raw_failed_executions"] == 1
    assert report["repair_executions"] == 1
    assert report["confirmation_executions"] == 1
    assert report["phase_coverage"] == {
        "proposal_agent_checker": True,
        "patch_exact_replay": True,
        "unchanged_skill_confirmation": True,
        "analysis": True,
    }
    assert report["clusters"][0]["repair_status"] == "repaired"
    assert report["clusters"][0]["confirmation_status"] == "confirmed"
    assert report["repair_validated_reproduced_clusters"] == 1

    path = write_development_gate_report(report, tmp_path)
    assert json.loads(path.read_text()) == report


def test_development_gate_accepts_campaigns_that_link_checker_receipts_externally():
    campaign, repairs, confirmations = _artifacts()
    for attempt in campaign["attempts"]:
        attempt["result"].pop("verdicts")
        attempt["result"].update(oracle_status="completed", n_verdicts=7)
    source_hash = canonical_json_hash(campaign)
    repairs["source_campaign_hash"] = source_hash
    confirmations["source_campaign_hash"] = source_hash

    report = build_development_gate_report(campaign, repairs, confirmations)

    assert report["phase_coverage"]["proposal_agent_checker"] is True


@pytest.mark.parametrize("mutation, message", [
    ("no-failure", "at least one raw failed"),
    ("no-repair", "at least one repair"),
    ("no-confirmation", "at least one confirmation"),
    ("wrong-source", "source campaign hash"),
])
def test_development_gate_fails_closed_without_every_phase(mutation, message):
    campaign, repairs, confirmations = _artifacts()
    if mutation == "no-failure":
        campaign["attempts"][0]["violated"] = []
        source_hash = canonical_json_hash(campaign)
        repairs["source_campaign_hash"] = source_hash
        confirmations["source_campaign_hash"] = source_hash
    elif mutation == "no-repair":
        repairs["failed_public_executions"] = 0
        repairs["repair_executions"] = 0
        repairs["repairs"] = []
    elif mutation == "no-confirmation":
        confirmations["confirmation_executions"] = 0
        confirmations["clusters"] = []
    else:
        repairs["source_campaign_hash"] = "0" * 64

    with pytest.raises(DevelopmentGateError, match=message):
        build_development_gate_report(campaign, repairs, confirmations)
