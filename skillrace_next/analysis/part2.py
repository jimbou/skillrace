from itertools import combinations
from statistics import mean, median
from typing import Any


def summarize_part2(
    methods: tuple[str, ...],
    heldout_rows: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    stage_costs: dict[str, float | int],
) -> dict[str, Any]:
    labels = ("s0", *methods)
    outcomes: dict[str, dict[tuple[str, int], bool]] = {
        label: {} for label in labels
    }
    for row in heldout_rows:
        label = row["method"]
        if label not in outcomes:
            raise ValueError("held-out result has an unknown method")
        key = (row["test_id"], row["repetition"])
        if key in outcomes[label] or not isinstance(row.get("passed"), bool):
            raise ValueError("held-out result is duplicated or invalid")
        outcomes[label][key] = row["passed"]
    baseline_keys = set(outcomes["s0"])
    if not baseline_keys or any(set(outcomes[label]) != baseline_keys for label in methods):
        raise ValueError("held-out result cells must match the S0 baseline")

    tests = sorted({test_id for test_id, _ in baseline_keys})
    repetitions = sorted({repetition for _, repetition in baseline_keys})
    per_test: dict[str, dict[str, float]] = {}
    all_tests: dict[str, float] = {}
    scenario_mean: dict[str, float] = {}
    scenario_median: dict[str, float] = {}
    for label in labels:
        rates = {
            test_id: sum(
                outcomes[label][(test_id, repetition)] for repetition in repetitions
            )
            / len(repetitions)
            for test_id in tests
        }
        per_test[label] = rates
        all_tests[label] = sum(
            all(outcomes[label][(test_id, repetition)] for test_id in tests)
            for repetition in repetitions
        ) / len(repetitions)
        scenario_mean[label] = mean(rates.values())
        scenario_median[label] = median(rates.values())

    pairwise = []
    for method_a, method_b in combinations(methods, 2):
        a_wins = sum(
            outcomes[method_a][key] and not outcomes[method_b][key]
            for key in baseline_keys
        )
        b_wins = sum(
            outcomes[method_b][key] and not outcomes[method_a][key]
            for key in baseline_keys
        )
        pairwise.append(
            {
                "method_a": method_a,
                "method_b": method_b,
                "a_wins": a_wins,
                "b_wins": b_wins,
                "ties": len(baseline_keys) - a_wins - b_wins,
            }
        )

    accepted = {method: 0 for method in methods}
    rejected = {method: 0 for method in methods}
    unresolved = {method: 0 for method in methods}
    for step in steps:
        method = step["method"]
        if step["decision"] == "accepted":
            accepted[method] += 1
        elif step["decision"] == "rejected":
            rejected[method] += 1
        elif step["decision"] == "unresolved":
            unresolved[method] += 1

    return {
        "per_test_pass_rate": per_test,
        "all_tests_pass_rate": all_tests,
        "scenario_mean": scenario_mean,
        "scenario_median": scenario_median,
        "pairwise_wins": pairwise,
        "regressions_from_s0": {
            method: sum(
                outcomes["s0"][key] and not outcomes[method][key]
                for key in baseline_keys
            )
            for method in methods
        },
        "accepted_revisions": accepted,
        "rejected_revisions": rejected,
        "unresolved_revisions": unresolved,
        "cost": dict(stage_costs),
    }
