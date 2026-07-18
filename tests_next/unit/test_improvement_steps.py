from skillrace_next.analysis.part2 import summarize_part2


def test_part2_metrics_compare_final_methods_with_shared_s0_baseline() -> None:
    rows = [
        {"method": "s0", "test_id": "h1", "repetition": 0, "passed": True, "cost": 1},
        {"method": "s0", "test_id": "h2", "repetition": 0, "passed": False, "cost": 1},
        {"method": "random", "test_id": "h1", "repetition": 0, "passed": False, "cost": 2},
        {"method": "random", "test_id": "h2", "repetition": 0, "passed": True, "cost": 2},
        {"method": "verigrey", "test_id": "h1", "repetition": 0, "passed": True, "cost": 2},
        {"method": "verigrey", "test_id": "h2", "repetition": 0, "passed": True, "cost": 2},
        {"method": "skillrace", "test_id": "h1", "repetition": 0, "passed": True, "cost": 2},
        {"method": "skillrace", "test_id": "h2", "repetition": 0, "passed": False, "cost": 2},
    ]
    steps = [
        {"method": "random", "decision": "accepted"},
        {"method": "random", "decision": "rejected"},
        {"method": "verigrey", "decision": "unresolved"},
        {"method": "skillrace", "decision": "retained"},
    ]

    summary = summarize_part2(
        ("random", "verigrey", "skillrace"),
        rows,
        steps,
        {"agent": 10, "patch": 4, "replay": 3, "heldout": 14},
    )

    assert summary["per_test_pass_rate"]["s0"] == {"h1": 1.0, "h2": 0.0}
    assert summary["per_test_pass_rate"]["verigrey"] == {"h1": 1.0, "h2": 1.0}
    assert summary["all_tests_pass_rate"]["verigrey"] == 1.0
    assert summary["all_tests_pass_rate"]["random"] == 0.0
    assert summary["scenario_mean"]["random"] == 0.5
    assert summary["scenario_median"]["random"] == 0.5
    assert summary["regressions_from_s0"] == {
        "random": 1,
        "verigrey": 0,
        "skillrace": 0,
    }
    random_verigrey = next(
        item
        for item in summary["pairwise_wins"]
        if item["method_a"] == "random" and item["method_b"] == "verigrey"
    )
    assert random_verigrey == {
        "method_a": "random",
        "method_b": "verigrey",
        "a_wins": 0,
        "b_wins": 1,
        "ties": 1,
    }
    assert summary["accepted_revisions"] == {"random": 1, "verigrey": 0, "skillrace": 0}
    assert summary["rejected_revisions"] == {"random": 1, "verigrey": 0, "skillrace": 0}
    assert summary["unresolved_revisions"] == {"random": 0, "verigrey": 1, "skillrace": 0}
    assert summary["cost"] == {"agent": 10, "patch": 4, "replay": 3, "heldout": 14}
