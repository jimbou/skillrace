"""Scenario-level paired analysis for the lean, one-execution RQ3 design."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from statistics import fmean
from typing import Any

from .io_utils import atomic_write_json, file_hash
from .rq3 import (
    EVALUATION_CONDITIONS,
    load_rq3_manifest,
    verify_rq3_evaluation_artifacts,
)


STATUS_VALUES = ("completed", "timeout", "error", "inconclusive", "missing")
SECONDARY_METHOD_COMPARISONS = {
    "skillrace_vs_greybox": ("skillrace-feedback", "greybox-feedback"),
    "skillrace_vs_random": ("skillrace-feedback", "random-feedback"),
}


class AnalysisError(ValueError):
    """Raised when raw RQ3 rows cannot support the frozen paired analysis."""


def records_from_rq3_manifest(
    path: str | pathlib.Path, *, scenario_dir: str | pathlib.Path
) -> list[dict[str, Any]]:
    """Verify one linked manifest and project its committed test results to rows."""

    manifest_path = pathlib.Path(path)
    manifest = verify_rq3_evaluation_artifacts(
        manifest_path,
        scenario_dir=scenario_dir,
        require_complete=False,
    )
    evaluations = manifest.get("evaluations")
    if not isinstance(evaluations, Mapping) or tuple(evaluations) != EVALUATION_CONDITIONS:
        raise AnalysisError("RQ3 manifest has the wrong evaluation conditions")
    scenario = manifest.get("scenario_id")
    replication = manifest.get("replication")
    if not isinstance(scenario, str) or not isinstance(replication, int):
        raise AnalysisError("RQ3 manifest lacks scenario/replication identity")
    root = manifest_path.parent
    rows: list[dict[str, Any]] = []
    for condition in EVALUATION_CONDITIONS:
        if condition == "zero-shot":
            search_cost = confirmation_cost = repair_cost = revision_cost = 0.0
        else:
            producer = condition.removesuffix("-feedback")
            campaigns = manifest.get("campaigns", {})
            revisions = manifest.get("revisions", {})
            if not isinstance(campaigns, Mapping) or not isinstance(revisions, Mapping):
                raise AnalysisError("RQ3 manifest lacks campaign/revision cost links")
            search_cost = float(campaigns.get(producer, {}).get("cost_provider_credits", 0.0) or 0.0)
            feedback = manifest.get("feedback_envelopes", {})
            if not isinstance(feedback, Mapping):
                raise AnalysisError("RQ3 manifest lacks feedback cost links")
            confirmation_cost = float(
                feedback.get(producer, {}).get("confirmation_cost_provider_credits", 0.0) or 0.0
            )
            repairs = manifest.get("repairs", {})
            if not isinstance(repairs, Mapping):
                raise AnalysisError("RQ3 manifest lacks repair cost links")
            repair_cost = float(
                repairs.get(producer, {}).get("costs", {}).get("total_provider_credits", 0.0)
                or 0.0
            )
            revision_cost = float(revisions.get(producer, {}).get("cost_provider_credits", 0.0) or 0.0)
        tests = evaluations[condition].get("tests")
        if not isinstance(tests, Mapping):
            raise AnalysisError(f"{condition} manifest tests are malformed")
        for test_id, link in tests.items():
            if not isinstance(test_id, str) or not isinstance(link, Mapping):
                raise AnalysisError(f"{condition} manifest test link is malformed")
            test_name = test_id.rsplit("/", 1)[-1]
            if not re.fullmatch(r"t(?:10|[1-9])", test_name):
                raise AnalysisError(f"unsafe or unstable hidden test ID: {test_id}")
            result_path = (
                root / "evaluations" / condition / "runs" / test_name / "result.json"
            )
            expected_hash = link.get("result_hash")
            if not result_path.is_file():
                if expected_hash is not None or link.get("execution_count", 0) != 0:
                    raise AnalysisError(
                        f"committed result is missing for {condition}/{test_id}"
                    )
                rows.append(
                    {
                        "scenario_id": scenario,
                        "replication": replication,
                        "test_id": test_id,
                        "condition": condition,
                        "status": "missing",
                        "functional_pass": None,
                        "strict_pass": None,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cost_provider_credits": 0.0,
                        "wall_seconds": 0.0,
                        "search_cost_provider_credits": search_cost,
                        "confirmation_cost_provider_credits": confirmation_cost,
                        "repair_cost_provider_credits": repair_cost,
                        "feedback_production_cost_provider_credits": search_cost,
                        "revision_cost_provider_credits": revision_cost,
                    }
                )
                continue
            actual_hash = file_hash(result_path)
            if actual_hash != expected_hash:
                raise AnalysisError(f"result hash mismatch for {condition}/{test_id}")
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise AnalysisError(f"malformed result for {condition}/{test_id}") from error
            if (
                not isinstance(result, Mapping)
                or result.get("schema") != "skillrace-hidden-result/1"
                or result.get("test_id") != test_id
                or link.get("execution_count") != 1
            ):
                raise AnalysisError(f"result identity mismatch for {condition}/{test_id}")
            grade = result.get("grade")
            if not isinstance(grade, Mapping):
                raise AnalysisError(f"result grade missing for {condition}/{test_id}")
            rows.append(
                {
                    "scenario_id": scenario,
                    "replication": replication,
                    "test_id": test_id,
                    "condition": condition,
                    "status": result.get("status"),
                    "functional_pass": grade.get("functional_pass"),
                    "strict_pass": grade.get("strict_pass"),
                    "input_tokens": result.get("input_tokens", 0),
                    "output_tokens": result.get("output_tokens", 0),
                    "cost_provider_credits": result.get("cost_provider_credits", 0.0),
                    "wall_seconds": result.get("wall_seconds", 0.0),
                    "search_cost_provider_credits": search_cost,
                    "confirmation_cost_provider_credits": confirmation_cost,
                    "repair_cost_provider_credits": repair_cost,
                    "feedback_production_cost_provider_credits": search_cost,
                    "revision_cost_provider_credits": revision_cost,
                }
            )
    return rows


def records_from_rq3_manifests(
    paths: Iterable[str | pathlib.Path],
    *,
    scenarios_root: str | pathlib.Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = pathlib.Path(scenarios_root)
    for path in paths:
        manifest = load_rq3_manifest(path)
        scenario = manifest.get("scenario_id")
        if not isinstance(scenario, str) or not scenario:
            raise AnalysisError(f"RQ3 manifest has no scenario identity: {path}")
        rows.extend(
            records_from_rq3_manifest(path, scenario_dir=root / scenario)
        )
    return rows


def _mean(values: Sequence[float]) -> float | None:
    return fmean(values) if values else None


def _validate_rows(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str, str]] = set()
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping):
            raise AnalysisError(f"analysis row {index} is not an object")
        scenario = raw.get("scenario_id")
        test_id = raw.get("test_id")
        replication = raw.get("replication")
        condition = raw.get("condition")
        status = raw.get("status", "missing")
        if not isinstance(scenario, str) or not scenario:
            raise AnalysisError(f"analysis row {index} has no scenario_id")
        if not isinstance(test_id, str) or not test_id.startswith(f"{scenario}/"):
            raise AnalysisError(f"analysis row {index} has an invalid test_id")
        if not isinstance(replication, int) or replication <= 0:
            raise AnalysisError(f"analysis row {index} has an invalid replication")
        if condition not in EVALUATION_CONDITIONS:
            raise AnalysisError(f"analysis row {index} has a non-headline condition")
        if status not in STATUS_VALUES:
            raise AnalysisError(f"analysis row {index} has an invalid status")
        key = (scenario, replication, test_id, condition)
        if key in seen:
            raise AnalysisError(f"duplicate hidden-test execution: {key}")
        seen.add(key)
        row = dict(raw)
        for field in ("functional_pass", "strict_pass"):
            value = row.get(field)
            if value not in {True, False, None}:
                raise AnalysisError(f"analysis row {index} has invalid {field}")
            if status in {"timeout", "error", "inconclusive", "missing"} and value is not None:
                raise AnalysisError(
                    f"analysis row {index} silently scores {status} as {field}={value}"
                )
        for field in ("input_tokens", "output_tokens"):
            value = row.get(field, 0)
            if not isinstance(value, int) or value < 0:
                raise AnalysisError(f"analysis row {index} has invalid {field}")
            row[field] = value
        for field in (
            "cost_provider_credits",
            "wall_seconds",
            "search_cost_provider_credits",
            "confirmation_cost_provider_credits",
            "repair_cost_provider_credits",
            "feedback_production_cost_provider_credits",
            "revision_cost_provider_credits",
        ):
            value = row.get(field, 0.0)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise AnalysisError(f"analysis row {index} has invalid {field}")
            row[field] = float(value)
        rows.append(row)
    if not rows:
        raise AnalysisError("analysis requires at least one result row")
    experiment_units = {
        (str(row["scenario_id"]), int(row["replication"])) for row in rows
    }
    existing = {
        (
            str(row["scenario_id"]),
            int(row["replication"]),
            str(row["test_id"]),
            str(row["condition"]),
        )
        for row in rows
    }
    for scenario, replication in experiment_units:
        expected = {
            (scenario, replication, f"{scenario}/t{number}", condition)
            for number in range(1, 11)
            for condition in EVALUATION_CONDITIONS
        }
        actual = {
            row for row in existing if row[0] == scenario and row[1] == replication
        }
        if actual != expected:
            raise AnalysisError(
                f"analysis schedule for {scenario}/replication-{replication} must be "
                "exactly four conditions across stable t1..t10"
            )
    return sorted(
        rows,
        key=lambda row: (
            row["scenario_id"],
            row["replication"],
            row["test_id"],
            EVALUATION_CONDITIONS.index(row["condition"]),
        ),
    )


def _condition_summary(
    rows: Sequence[Mapping[str, Any]], expected_cells: set[tuple[str, int, str]]
) -> dict[str, Any]:
    status = Counter(str(row.get("status", "missing")) for row in rows)
    present = {
        (str(row["scenario_id"]), int(row["replication"]), str(row["test_id"]))
        for row in rows
    }
    status["missing"] += len(expected_cells - present)
    functional = [row["functional_pass"] for row in rows if row.get("functional_pass") is not None]
    strict = [row["strict_pass"] for row in rows if row.get("strict_pass") is not None]
    upstream: dict[tuple[str, int], tuple[float, float, float, float]] = {}
    for row in rows:
        key = (str(row["scenario_id"]), int(row["replication"]))
        value = (
            float(
                row.get(
                    "search_cost_provider_credits",
                    row.get("feedback_production_cost_provider_credits", 0.0),
                )
            ),
            float(row.get("confirmation_cost_provider_credits", 0.0)),
            float(row.get("repair_cost_provider_credits", 0.0)),
            float(row.get("revision_cost_provider_credits", 0.0)),
        )
        if key in upstream and upstream[key] != value:
            raise AnalysisError(f"inconsistent testing/revision cost for {key}")
        upstream[key] = value
    search_cost = sum(value[0] for value in upstream.values())
    confirmation_cost = sum(value[1] for value in upstream.values())
    repair_cost = sum(value[2] for value in upstream.values())
    revision_cost = sum(value[3] for value in upstream.values())
    evaluation_cost = sum(float(row.get("cost_provider_credits", 0.0)) for row in rows)
    scheduled = len(expected_cells)
    functional_passes = sum(value is True for value in functional)
    strict_passes = sum(value is True for value in strict)
    return {
        "scheduled": scheduled,
        "recorded": len(rows),
        "scored": len(functional),
        "functional_passes": functional_passes,
        "functional_pass_rate": (
            functional_passes / scheduled if scheduled else None
        ),
        "available_case_functional_pass_rate": (
            functional_passes / len(functional) if functional else None
        ),
        "strict_scored": len(strict),
        "strict_passes": strict_passes,
        "strict_pass_rate": (
            strict_passes / scheduled if scheduled else None
        ),
        "available_case_strict_pass_rate": (
            strict_passes / len(strict) if strict else None
        ),
        "status_counts": {name: status.get(name, 0) for name in STATUS_VALUES},
        "input_tokens": sum(int(row.get("input_tokens", 0)) for row in rows),
        "output_tokens": sum(int(row.get("output_tokens", 0)) for row in rows),
        "evaluation_cost_provider_credits": round(evaluation_cost, 6),
        "search_cost_provider_credits": round(search_cost, 6),
        "confirmation_cost_provider_credits": round(confirmation_cost, 6),
        "repair_cost_provider_credits": round(repair_cost, 6),
        "feedback_production_cost_provider_credits": round(search_cost, 6),
        "revision_cost_provider_credits": round(revision_cost, 6),
        "testing_revision_evaluation_cost_provider_credits": round(
            search_cost
            + confirmation_cost
            + repair_cost
            + revision_cost
            + evaluation_cost,
            6,
        ),
        "inclusive_total_cost_provider_credits": round(
            search_cost
            + confirmation_cost
            + repair_cost
            + revision_cost
            + evaluation_cost,
            6,
        ),
        # Backward-compatible alias for the per-hidden-evaluation portion.
        "cost_provider_credits": round(evaluation_cost, 6),
        "wall_seconds": round(sum(float(row.get("wall_seconds", 0.0)) for row in rows), 3),
    }


def _paired_effect(
    rows: Sequence[Mapping[str, Any]], treatment: str, control: str
) -> dict[str, Any]:
    by_test: dict[tuple[str, int, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        key = (str(row["scenario_id"]), int(row["replication"]), str(row["test_id"]))
        by_test[key][str(row["condition"])] = row
    functional: list[float] = []
    strict: list[float] = []
    available_functional: list[float] = []
    available_strict: list[float] = []
    unavailable_pairs = 0
    for test_rows in by_test.values():
        treated = test_rows.get(treatment)
        controlled = test_rows.get(control)
        if treated is None or controlled is None:
            unavailable_pairs += 1
            continue
        treated_functional = treated.get("functional_pass")
        control_functional = controlled.get("functional_pass")
        functional.append(float(treated_functional is True) - float(control_functional is True))
        if isinstance(treated_functional, bool) and isinstance(control_functional, bool):
            available_functional.append(float(treated_functional) - float(control_functional))
        else:
            unavailable_pairs += 1
        treated_strict = treated.get("strict_pass")
        control_strict = controlled.get("strict_pass")
        strict.append(float(treated_strict is True) - float(control_strict is True))
        if isinstance(treated_strict, bool) and isinstance(control_strict, bool):
            available_strict.append(float(treated_strict) - float(control_strict))
    return {
        "functional_difference": _mean(functional),
        "strict_difference": _mean(strict),
        "paired_test_count": len(functional),
        "strict_paired_test_count": len(strict),
        "unscored_or_missing_pairs": unavailable_pairs,
        "available_case_functional_difference": _mean(available_functional),
        "available_case_strict_difference": _mean(available_strict),
        "available_case_pair_count": len(available_functional),
    }


def _aggregate_scenario_effects(
    scenario_rows: Sequence[Mapping[str, Any]], treatment: str, control: str
) -> dict[str, Any]:
    by_replication: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in scenario_rows:
        by_replication[int(row["replication"])].append(row)
    replication_effects = [
        _paired_effect(by_replication[replication], treatment, control)
        for replication in sorted(by_replication)
    ]
    functional = [
        effect["functional_difference"]
        for effect in replication_effects
        if effect["functional_difference"] is not None
    ]
    strict = [
        effect["strict_difference"]
        for effect in replication_effects
        if effect["strict_difference"] is not None
    ]
    return {
        "functional_difference": _mean(functional),
        "strict_difference": _mean(strict),
        "paired_test_count": sum(effect["paired_test_count"] for effect in replication_effects),
        "strict_paired_test_count": sum(
            effect["strict_paired_test_count"] for effect in replication_effects
        ),
        "unscored_or_missing_pairs": sum(
            effect["unscored_or_missing_pairs"] for effect in replication_effects
        ),
        "replication_count": len(by_replication),
    }


def _headline_aggregate(
    scenario_effects: Sequence[Mapping[str, Any]], key: str
) -> dict[str, Any]:
    available = [
        row[key]
        for row in scenario_effects
        if row[key]["functional_difference"] is not None
    ]
    strict = [
        row[key]["strict_difference"]
        for row in scenario_effects
        if row[key]["strict_difference"] is not None
    ]
    return {
        "functional_difference": _mean(
            [effect["functional_difference"] for effect in available]
        ),
        "strict_difference": _mean(strict),
        "scenario_count": len(available),
        "paired_test_count": sum(effect["paired_test_count"] for effect in available),
        "unscored_or_missing_pairs": sum(
            effect["unscored_or_missing_pairs"] for effect in available
        ),
    }


def analyze_rq3(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Compute paired differences with scenarios—not 100 tests—as top-level units."""

    rows = _validate_rows(records)
    expected_cells = {
        (str(row["scenario_id"]), int(row["replication"]), str(row["test_id"]))
        for row in rows
    }
    condition_summaries = {
        condition: _condition_summary(
            [row for row in rows if row["condition"] == condition], expected_cells
        )
        for condition in EVALUATION_CONDITIONS
    }
    by_scenario: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_scenario[str(row["scenario_id"])].append(row)
    scenario_effects: list[dict[str, Any]] = []
    for scenario in sorted(by_scenario):
        scenario_rows = by_scenario[scenario]
        effects: dict[str, Any] = {
            "scenario_id": scenario,
            "replications": sorted({int(row["replication"]) for row in scenario_rows}),
            "primary": {},
            "zero_shot_deltas": {},
            "condition_summaries": {},
        }
        scenario_cells = {
            (str(row["scenario_id"]), int(row["replication"]), str(row["test_id"]))
            for row in scenario_rows
        }
        for key, (treatment, control) in SECONDARY_METHOD_COMPARISONS.items():
            effects["primary"][key] = _aggregate_scenario_effects(
                scenario_rows, treatment, control
            )
        for treatment in EVALUATION_CONDITIONS[1:]:
            effects["zero_shot_deltas"][treatment] = _aggregate_scenario_effects(
                scenario_rows, treatment, "zero-shot"
            )
        effects["condition_summaries"] = {
            condition: _condition_summary(
                [row for row in scenario_rows if row["condition"] == condition],
                scenario_cells,
            )
            for condition in EVALUATION_CONDITIONS
        }
        scenario_effects.append(effects)

    secondary_method_contrasts = {
        key: _headline_aggregate(
            [
                {key: scenario["primary"][key]}
                for scenario in scenario_effects
            ],
            key,
        )
        for key in SECONDARY_METHOD_COMPARISONS
    }
    primary_zero_shot_change = {
        treatment: _headline_aggregate(
            [
                {treatment: scenario["zero_shot_deltas"][treatment]}
                for scenario in scenario_effects
            ],
            treatment,
        )
        for treatment in EVALUATION_CONDITIONS[1:]
    }
    return {
        "schema": "skillrace-rq3-analysis/1",
        "headline": {
            "unit": "scenario",
            "one_execution_per_hidden_test": True,
            "primary_zero_shot_change": primary_zero_shot_change,
            "secondary_method_contrasts": secondary_method_contrasts,
            "inference": "descriptive paired scenario effects; no p-values without independent replication",
        },
        "condition_summaries": condition_summaries,
        "scenario_effects": scenario_effects,
        "limitations": [
            "Error, timeout, inconclusive, and missing executions are conservative non-passes in headline rates and paired differences; available-case sensitivity is reported separately.",
            "Hidden tests within a scenario are not treated as independent top-level samples.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze lean RQ3 JSON rows")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--records", help="JSON array of per-test condition rows")
    source.add_argument(
        "--manifest",
        action="append",
        help="verified rq3-manifest.json (repeat for multiple scenarios/replications)",
    )
    parser.add_argument(
        "--scenarios-root",
        help="required with --manifest so current hidden contracts can be re-hashed",
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.records:
        records = json.loads(pathlib.Path(args.records).read_text(encoding="utf-8"))
    else:
        if not args.scenarios_root:
            parser.error("--scenarios-root is required with --manifest")
        records = records_from_rq3_manifests(
            args.manifest,
            scenarios_root=args.scenarios_root,
        )
    result = analyze_rq3(records)
    atomic_write_json(args.out, result)


if __name__ == "__main__":
    main()
