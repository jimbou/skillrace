import json

from skillrace.aggregate import aggregate, summarize_campaign


def test_first_execution_is_one_not_zero_for_legacy_campaign():
    summary = summarize_campaign(
        {
            "method": "random",
            "skill": "demo",
            "budget": 3,
            "iterations": [
                {"i": 0, "violated": ["p1"]},
                {"i": 1, "violated": []},
                {"i": 2, "violated": []},
            ],
        }
    )
    assert summary["runs_to_first_violation"] == 1
    assert summary["first_violation_observed"] is True


def test_no_violation_is_right_censored_at_counted_runs():
    summary = summarize_campaign(
        {
            "method": "random",
            "skill": "demo",
            "budget": 3,
            "iterations": [
                {"i": 0, "violated": []},
                {"i": 1, "violated": []},
                {"i": 2, "violated": []},
            ],
        }
    )
    assert summary["runs_to_first_violation"] == 3
    assert summary["first_violation_observed"] is False


def test_attempt_records_count_only_consumed_agent_executions():
    summary = summarize_campaign(
        {
            "method": "skillrace",
            "skill": "demo",
            "budget": 2,
            "status": "completed",
            "attempts": [
                {"attempt_id": "a1", "consume_budget": False, "violated": ["infra"]},
                {"attempt_id": "a2", "consume_budget": True, "violated": []},
                {"attempt_id": "a3", "consume_budget": False, "violated": []},
                {"attempt_id": "a4", "consume_budget": True, "violated": ["p1"]},
            ],
            "iterations": [],
        }
    )
    assert summary["runs"] == 2
    assert summary["runs_to_first_violation"] == 2
    assert summary["first_violation_observed"] is True
    assert summary["distinct_violated"] == ["p1"]
    assert summary["complete"] is True
    assert summary["headline_eligible"] is True


def test_pooled_output_preserves_observed_and_censored_records(tmp_path):
    observed = tmp_path / "random" / "observed"
    censored = tmp_path / "random" / "censored"
    observed.mkdir(parents=True)
    censored.mkdir(parents=True)
    (observed / "campaign.json").write_text(
        json.dumps(
            {
                "method": "random",
                "skill": "observed",
                "iterations": [{"i": 0, "violated": ["p1"]}],
            }
        )
    )
    (censored / "campaign.json").write_text(
        json.dumps(
            {
                "method": "random",
                "skill": "censored",
                "iterations": [
                    {"i": 0, "violated": []},
                    {"i": 1, "violated": []},
                ],
            }
        )
    )

    pooled = aggregate(tmp_path)["pooled_by_method"]["random"]

    assert pooled["first_violation_records"] == [
        {"skill": "censored", "runs": 2, "observed": False},
        {"skill": "observed", "runs": 1, "observed": True},
    ]
    assert "median_runs_to_first_violation" not in pooled


def test_aborted_new_campaign_is_reported_but_excluded_from_headlines(tmp_path):
    complete = tmp_path / "random" / "complete"
    aborted = tmp_path / "random" / "aborted"
    complete.mkdir(parents=True)
    aborted.mkdir(parents=True)
    (complete / "campaign.json").write_text(
        json.dumps(
            {
                "method": "random",
                "skill": "complete",
                "budget": 1,
                "status": "completed",
                "attempts": [
                    {"consume_budget": True, "violated": ["real-defect"]}
                ],
            }
        )
    )
    (aborted / "campaign.json").write_text(
        json.dumps(
            {
                "method": "random",
                "skill": "aborted",
                "budget": 3,
                "status": "aborted_pre_agent_attempt_cap",
                "attempts": [
                    {"consume_budget": True, "violated": ["partial-defect"]},
                    {"consume_budget": False, "violated": []},
                ],
            }
        )
    )

    result = aggregate(tmp_path)
    pooled = result["pooled_by_method"]["random"]

    assert pooled["campaigns_total"] == 2
    assert pooled["campaigns_eligible"] == 1
    assert pooled["distinct_violated_pooled"] == 1
    assert pooled["first_violation_records"] == [
        {"skill": "complete", "runs": 1, "observed": True}
    ]
    assert result["incomplete_campaigns"] == [
        {
            "method": "random",
            "skill": "aborted",
            "status": "aborted_pre_agent_attempt_cap",
            "runs": 1,
            "budget": 3,
            "completion_reason": "status=aborted_pre_agent_attempt_cap; counted=1/3",
        }
    ]


def test_completed_new_campaign_without_defect_is_right_censored():
    summary = summarize_campaign(
        {
            "method": "random",
            "skill": "complete",
            "budget": 2,
            "status": "completed",
            "attempts": [
                {"consume_budget": True, "violated": []},
                {"consume_budget": True, "violated": []},
            ],
        }
    )
    assert summary["complete"] is True
    assert summary["headline_eligible"] is True
    assert summary["runs_to_first_violation"] == 2
    assert summary["first_violation_observed"] is False
    assert summary["right_censored"] is True


def test_legacy_campaign_without_status_remains_headline_eligible():
    summary = summarize_campaign(
        {
            "method": "random",
            "skill": "legacy",
            "iterations": [{"i": 0, "violated": []}],
        }
    )
    assert summary["complete"] is True
    assert summary["headline_eligible"] is True
    assert summary["completion_reason"] == "legacy status absent"
