from __future__ import annotations

import copy

import pytest

from skillrace.io_utils import canonical_json_hash
from skillrace.feedback import (
    BYTE_BUDGET_ID,
    FeedbackEnvelopeError,
    build_feedback_envelope,
    envelope_byte_count,
    validate_feedback_envelope,
)


def _campaign(method: str) -> dict:
    return {
        "method": method,
        "budget": 30,
        "protocol_hash": "a" * 64,
        "complete": True,
        "attempts": [
            {
                "attempt_id": "attempt-000001",
                "i": 1,
                "consume_budget": True,
                "candidate_id": "candidate-b",
                "runner_status": "completed",
                "oracle_status": "inconclusive",
                "inconclusive": ["p-unknown"],
                "provenance": {
                    "task_nl": "second task",
                    "env_nl": "second environment",
                },
                "seconds": 4.5,
            },
            {
                "attempt_id": "attempt-000002",
                "i": 0,
                "consume_budget": True,
                "candidate_id": "candidate-a",
                "runner_status": "completed",
                "oracle_status": "completed",
                "violated": ["p-confirmed", "p-unconfirmed"],
                "regrade": {
                    "k": 1,
                    "reproduced": {"p-confirmed": 1, "p-unconfirmed": 0},
                },
                "reproducible": ["p-confirmed"],
                "provenance": {
                    "task_nl": "first task",
                    "env_nl": "first environment",
                    "mutation": "change a public input shape",
                    "guard": "input shape differs",
                    "targeted_property": "p-confirmed",
                },
                "classification": {
                    "branch_outcome": "different-new-branch",
                    "targeting": "serendipitous",
                },
                "seconds": 3.0,
                "compile_cost_usd": 0.02,
            },
            {
                "attempt_id": "attempt-000003",
                "i": 2,
                "consume_budget": False,
                "generation_status": "sanity_rejected",
                "runner_status": "not_started",
            },
        ],
        "iterations": [],
        "generator_state": {
            "novelty": {"tools": ["read", "write"]},
            "branch_outcomes": ["different-new-branch"],
        },
        "totals": {"runs": 2, "attempts": 3},
    }


def _confirmations(campaign: dict) -> dict:
    return {
        "schema": "skillrace-confirmations/1",
        "source_campaign_hash": canonical_json_hash(campaign),
        "confirmation_executions": 1,
        "confirmation_executions_counted_in_search_budget": False,
        "clusters": [
            {
                "cluster_id": "cluster-one",
                "execution_ordinal": 1,
                "property_id": "p-confirmed",
                "failure_signature": "b" * 64,
                "failure_summary": "confirmed failure",
                "representative_candidate_id": "candidate-a",
                "case_hash": "c" * 64,
                "status": "confirmed",
                "reproduction_count": 1,
            }
        ],
        "costs": {"total_usd": 0.02},
    }


def test_all_methods_have_the_same_ordered_schema_and_limits():
    envelopes = [
        build_feedback_envelope(_campaign(method), max_bytes=3600)
        for method in ("random", "greybox", "skillrace")
    ]

    assert [list(envelope) for envelope in envelopes] == [list(envelopes[0])] * 3
    assert [list(envelope["method_evidence"]) for envelope in envelopes] == [
        ["tool_novelty", "guard_mutations", "branch_outcomes"]
    ] * 3
    assert [envelope["accounting"]["limits"] for envelope in envelopes] == [
        envelopes[0]["accounting"]["limits"]
    ] * 3
    assert all("method" not in envelope for envelope in envelopes)


def test_feedback_budget_is_exact_canonical_utf8_bytes_not_a_fake_tokenizer():
    envelope = build_feedback_envelope(_campaign("skillrace"), max_bytes=3600)

    assert envelope["accounting"]["budget_unit"] == BYTE_BUDGET_ID
    assert envelope["accounting"]["used_bytes"] == envelope_byte_count(envelope)
    assert envelope["accounting"]["used_bytes"] <= envelope["accounting"]["max_bytes"]
    assert "tokenizer" not in envelope["accounting"]
    assert "used_tokens" not in envelope["accounting"]
    assert "max_tokens" not in envelope["accounting"]


def test_confirmed_requires_explicit_reproduction_and_stays_separate():
    campaign = _campaign("skillrace")
    envelope = build_feedback_envelope(
        campaign, max_bytes=3600, confirmations=_confirmations(campaign)
    )

    assert [row["property_id"] for row in envelope["confirmed_findings"]] == [
        "p-confirmed"
    ]
    assert all(
        row["property_id"] != "p-unconfirmed"
        for row in envelope["confirmed_findings"]
    )
    assert [row["property_id"] for row in envelope["inconclusive_findings"]] == [
        "p-unknown"
    ]
    assert envelope["confirmed_findings"][0]["reproduction_count"] == 1


def test_confirmed_feedback_preserves_representative_task_and_environment():
    campaign = _campaign("skillrace")
    confirmations = _confirmations(campaign)
    confirmations["clusters"][0].update(
        {
            "task_summary": "exercise quoted Unicode input",
            "environment_summary": "CLI starts with a malformed config",
        }
    )

    envelope = build_feedback_envelope(
        campaign, max_bytes=3600, confirmations=confirmations
    )

    finding = envelope["confirmed_findings"][0]
    assert finding["task_summary"] == "exercise quoted Unicode input"
    assert finding["environment_summary"] == "CLI starts with a malformed config"


def test_search_violation_without_frozen_confirmation_never_becomes_confirmed():
    envelope = build_feedback_envelope(_campaign("skillrace"), max_bytes=3600)

    assert envelope["confirmed_findings"] == []
    assert envelope["costs"]["confirmation_executions"] == 0


def test_envelope_is_deterministic_sorted_and_bounded_with_drop_metadata():
    campaign = _campaign("skillrace")
    for number in range(80):
        row = copy.deepcopy(campaign["attempts"][0])
        row["attempt_id"] = f"attempt-{number + 10:06d}"
        row["i"] = number + 10
        row["candidate_id"] = f"candidate-{number:03d}"
        row["provenance"]["task_nl"] = "task " + ("x" * 500)
        campaign["attempts"].append(row)

    first = build_feedback_envelope(campaign, max_bytes=3600)
    second = build_feedback_envelope(copy.deepcopy(campaign), max_bytes=3600)

    assert first == second
    assert envelope_byte_count(first) <= 3600
    assert first["accounting"]["used_bytes"] == envelope_byte_count(first)
    assert first["truncation"]["dropped"]["explored_situations"] > 0
    assert [row["execution_ordinal"] for row in first["explored_situations"]] == sorted(
        row["execution_ordinal"] for row in first["explored_situations"]
    )
    validate_feedback_envelope(first)


def test_byte_truncation_round_robins_before_repeating_a_large_section():
    campaign = _campaign("skillrace")
    campaign["attempts"] = []
    campaign["generator_state"] = {"novelty": {"tools": ["bash"]}}
    for index in range(30):
        campaign["attempts"].append(
            {
                "attempt_id": f"attempt-{index:06d}",
                "i": index,
                "consume_budget": True,
                "candidate_id": f"candidate-{index:03d}",
                "runner_status": "completed",
                "oracle_status": "completed",
                "provenance": {
                    "task_nl": "task " + "t" * 500,
                    "env_nl": "environment " + "e" * 500,
                    "guard": "guard " + "g" * 500,
                    "mutation": "mutation " + "m" * 500,
                    "targeted_property": "behavior",
                },
                "classification": {
                    "branch_outcome": "different_new_branch",
                    "targeting": "serendipitous",
                },
            }
        )
    campaign["totals"] = {"runs": 30, "attempts": 30}

    envelope = build_feedback_envelope(campaign, max_bytes=3600)

    assert envelope["explored_situations"]
    assert envelope["method_evidence"]["tool_novelty"]
    assert envelope["method_evidence"]["guard_mutations"]
    assert envelope["method_evidence"]["branch_outcomes"]
    assert envelope["accounting"]["used_bytes"] <= 3600


def test_envelope_rejects_non_headline_method_and_tampered_accounting():
    with pytest.raises(FeedbackEnvelopeError, match="producer"):
        build_feedback_envelope(_campaign("direct-property"), max_bytes=3600)

    envelope = build_feedback_envelope(_campaign("random"), max_bytes=3600)
    envelope["accounting"]["used_bytes"] += 1
    with pytest.raises(FeedbackEnvelopeError, match="used_bytes"):
        validate_feedback_envelope(envelope)


def test_envelope_validation_enforces_recorded_field_and_item_limits():
    campaign = _campaign("skillrace")
    envelope = build_feedback_envelope(
        campaign, max_bytes=3600, confirmations=_confirmations(campaign)
    )
    envelope["confirmed_findings"][0]["failure_summary"] = "x" * 321
    # Restore self-accounting so this specifically exercises the field limit.
    while True:
        count = envelope_byte_count(envelope)
        if envelope["accounting"]["used_bytes"] == count:
            break
        envelope["accounting"]["used_bytes"] = count
    with pytest.raises(FeedbackEnvelopeError, match="string field limit"):
        validate_feedback_envelope(envelope)

    envelope = build_feedback_envelope(
        campaign, max_bytes=3600, confirmations=_confirmations(campaign)
    )
    envelope["accounting"]["limits"]["max_confirmed_findings"] = 0
    while True:
        count = envelope_byte_count(envelope)
        if envelope["accounting"]["used_bytes"] == count:
            break
        envelope["accounting"]["used_bytes"] = count
    with pytest.raises(FeedbackEnvelopeError, match="item limit"):
        validate_feedback_envelope(envelope)
