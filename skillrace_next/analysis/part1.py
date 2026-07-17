from typing import Any


def summarize_part1(
    raw_candidates: list[dict[str, Any]],
    confirmed_bugs: list[dict[str, Any]],
    patches: list[dict[str, Any]],
    terminal_failures: list[dict[str, Any]],
    stage_costs: dict[str, float | int],
) -> dict[str, Any]:
    repaired = sum(item.get("decision") == "accepted" for item in patches)
    confirmed_count = len(confirmed_bugs)
    inconclusive = sum(
        item.get("status") == "inconclusive" for item in terminal_failures
    )
    infrastructure = sum(
        item.get("kind") == "infrastructure" for item in terminal_failures
    )
    return {
        "raw_candidates": len(raw_candidates),
        "confirmed_distinct_bugs": confirmed_count,
        "confirmed_repaired_bugs": repaired,
        "repair_success_rate": repaired / confirmed_count if confirmed_count else 0.0,
        "inconclusive_count": inconclusive,
        "infrastructure_failure_count": infrastructure,
        "stage_costs": dict(stage_costs),
    }
