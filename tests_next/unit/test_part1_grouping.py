from skillrace_next.analysis.part1 import summarize_part1
from skillrace_next.pipeline.part1 import group_failure_candidates


def candidate(candidate_id, run_id, property_group, signature, category):
    return {
        "candidate_id": candidate_id,
        "run_id": run_id,
        "method": "random",
        "s0_hash": "immutable-s0",
        "property_group": property_group,
        "failing_check_signature": signature,
        "root_cause_category": category,
    }


def test_groups_repeated_failures_by_exact_scientific_key() -> None:
    raw = [
        candidate("c2", "run-2", "output", "P1:wrong-bytes", "format_contract"),
        candidate("c1", "run-1", "output", "P1:wrong-bytes", "format_contract"),
        candidate("c3", "run-3", "output", "P1:wrong-bytes", "wrong_workflow"),
    ]

    groups = group_failure_candidates(raw)

    assert len(groups) == 2
    repeated = next(group for group in groups if group["key"][2] == "format_contract")
    assert repeated["key"] == ["output", "P1:wrong-bytes", "format_contract"]
    assert repeated["candidate_ids"] == ["c1", "c2"]
    assert repeated["representative_candidate_id"] == "c1"
    assert all(candidate["s0_hash"] == "immutable-s0" for candidate in raw)


def test_summary_keeps_raw_candidates_confirmed_bugs_and_repairs_separate() -> None:
    raw = [
        candidate("c1", "run-1", "output", "P1:wrong", "format_contract"),
        candidate("c2", "run-2", "output", "P1:wrong", "format_contract"),
        candidate("c3", "run-3", "other", "P2:missing", "instruction_missing"),
    ]
    confirmed = [
        {
            "group_key": ["output", "P1:wrong", "format_contract"],
            "representative_candidate_id": "c1",
        }
    ]
    patches = [{"candidate_id": "c1", "decision": "accepted"}]

    summary = summarize_part1(raw, confirmed, patches, [], {"agent_tokens": 20})

    assert summary["raw_candidates"] == 3
    assert summary["confirmed_distinct_bugs"] == 1
    assert summary["confirmed_repaired_bugs"] == 1
    assert summary["repair_success_rate"] == 1.0
    assert summary["inconclusive_count"] == 0
    assert summary["infrastructure_failure_count"] == 0
    assert summary["stage_costs"] == {"agent_tokens": 20}
