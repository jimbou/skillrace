from pathlib import Path
from typing import Any

from ..analysis.part1 import summarize_part1
from ..records import ExperimentConfig, SkillVersion
from ..storage import atomic_write_json


def group_failure_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = (
            candidate["property_group"],
            candidate["failing_check_signature"],
            candidate["root_cause_category"],
        )
        grouped.setdefault(key, []).append(candidate)
    groups: list[dict[str, Any]] = []
    for key in sorted(grouped):
        members = sorted(grouped[key], key=lambda item: item["candidate_id"])
        groups.append(
            {
                "key": list(key),
                "candidate_ids": [item["candidate_id"] for item in members],
                "representative_candidate_id": members[0]["candidate_id"],
            }
        )
    return groups


def run_part1(
    s0: SkillVersion,
    config: ExperimentConfig,
    output_dir: str | Path,
    *,
    propose: Any,
    execute: Any,
    check: Any,
    update_state: Any,
    confirm: Any,
    patch: Any,
) -> dict[str, Any]:
    output = Path(output_dir)
    if output.exists():
        raise ValueError("Part I output already exists")
    output.mkdir(parents=True)
    raw_candidates: list[dict[str, Any]] = []
    terminal_failures: list[dict[str, Any]] = []
    stage_costs: dict[str, float | int] = {"agent": 0, "patch": 0}
    for method in config.methods:
        method_dir = output / "methods" / method
        method_dir.mkdir(parents=True)
        state: dict[str, Any] = {}
        for slot in range(config.iteration_budget):
            slot_dir = method_dir / "runs" / str(slot)
            test = propose(method, state, s0, slot, slot_dir / "proposal")
            run = execute(method, s0, test, slot, slot_dir / "execution")
            if run.get("model_id") != config.model_id:
                raise ValueError("non-verifier model differs from track model")
            if run.get("skill_hash") != s0.tree_hash:
                raise ValueError("discovery run did not use immutable S0")
            stage_costs["agent"] += run.get("cost", 0)
            results = check(method, run, test, slot_dir / "checks")
            state = update_state(method, state, run, results, slot_dir / "state-update")
            atomic_write_json(method_dir / "state.json", state)
            for item in results:
                if item.get("status") == "fail":
                    raw_candidates.append(
                        {
                            "candidate_id": (
                                f"{method}:{run['run_id']}:{item['check_id']}"
                            ),
                            "run_id": run["run_id"],
                            "test_id": run["test_id"],
                            "method": method,
                            "s0_hash": s0.tree_hash,
                            "property_group": item["property_group"],
                            "failing_check_signature": item[
                                "failing_check_signature"
                            ],
                            "root_cause_category": item["root_cause_category"],
                        }
                    )
                elif item.get("status") == "inconclusive":
                    terminal_failures.append(dict(item))
    groups = group_failure_candidates(raw_candidates)
    by_id = {item["candidate_id"]: item for item in raw_candidates}
    confirmed_bugs: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []
    for group in groups:
        representative = by_id[group["representative_candidate_id"]]
        group_dir = output / "confirmed" / representative["candidate_id"].replace(
            ":", "-"
        )
        if not confirm(representative, group_dir / "confirmation"):
            continue
        confirmed_bugs.append(
            {
                "group_key": group["key"],
                "representative_candidate_id": representative["candidate_id"],
            }
        )
        patched = patch(representative, group_dir / "patch")
        if patched.get("model_id") != config.model_id or patched.get("backend") != "pi":
            raise ValueError("patcher must use the same-track Pi backend")
        stage_costs["patch"] += patched.get("cost", 0)
        patches.append(patched)
    summary = summarize_part1(
        raw_candidates,
        confirmed_bugs,
        patches,
        terminal_failures,
        stage_costs,
    )
    result = {
        "schema": "skillrace-part1/1",
        "s0_hash": s0.tree_hash,
        "raw_candidates": raw_candidates,
        "groups": groups,
        "confirmed_bugs": confirmed_bugs,
        "patches": patches,
        "summary": summary,
    }
    atomic_write_json(output / "summary.json", result)
    return result
