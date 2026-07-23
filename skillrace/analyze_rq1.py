"""Verified, repair-aware analysis for the three-method RQ1/RQ2 experiment."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import pathlib
import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from statistics import fmean
from typing import Any

from .io_utils import (
    atomic_write_json,
    atomic_write_text,
    canonical_json_hash,
    file_hash,
    resolve_campaign_path,
)
from .repair_validation import (
    RQ1_REPAIR_EVIDENCE_MAX_BYTES,
    select_failure_repairs,
    validate_repair_ledger,
)
from .revise_skill import package_hash
from .rq3_confirmation import validate_confirmation_ledger


METHODS = ("random", "greybox", "skillrace")
REPAIR_STATUSES = (
    "repaired",
    "same_failure",
    "different_failure",
    "timeout",
    "error",
    "inconclusive",
)


class RQ1AnalysisError(ValueError):
    """RQ1 inputs are incomplete, unpaired, or inconsistent."""


def discovery_curve(
    events: Iterable[tuple[int, str]], *, budget: int
) -> list[int]:
    """Return right-continuous distinct-cluster yield after executions 1..budget."""

    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        raise RQ1AnalysisError("discovery budget must be a positive integer")
    normalized: list[tuple[int, str]] = []
    for execution, cluster in events:
        if (
            isinstance(execution, bool)
            or not isinstance(execution, int)
            or execution < 1
            or execution > budget
            or not isinstance(cluster, str)
            or not cluster
        ):
            raise RQ1AnalysisError("discovery event is malformed or outside the budget")
        normalized.append((execution, cluster))
    observed: set[str] = set()
    curve: list[int] = []
    for execution in range(1, budget + 1):
        observed.update(cluster for ordinal, cluster in normalized if ordinal == execution)
        curve.append(len(observed))
    return curve


def normalized_auc(
    curve: Sequence[int], *, maximum_final_yield: int | None = None
) -> float:
    """Mean discovery curve divided by the compared maximum final yield."""

    if not curve:
        raise RQ1AnalysisError("AUC requires a nonempty discovery curve")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in curve
    ):
        raise RQ1AnalysisError("discovery curve values must be non-negative integers")
    if any(right < left for left, right in zip(curve, curve[1:])):
        raise RQ1AnalysisError("discovery curve must be monotone")
    denominator = curve[-1] if maximum_final_yield is None else maximum_final_yield
    if (
        isinstance(denominator, bool)
        or not isinstance(denominator, int)
        or denominator < 0
    ):
        raise RQ1AnalysisError("AUC normalization yield must be non-negative")
    if denominator == 0:
        return 0.0
    if any(value > denominator for value in curve):
        raise RQ1AnalysisError("AUC curve exceeds its normalization yield")
    return fmean(curve) / denominator


def survival_record(discoveries: Sequence[bool]) -> dict[str, Any]:
    """Encode one-based time-to-first discovery with right censoring."""

    if not discoveries or any(not isinstance(value, bool) for value in discoveries):
        raise RQ1AnalysisError("survival input must be a nonempty boolean sequence")
    for ordinal, observed in enumerate(discoveries, start=1):
        if observed:
            return {"time": ordinal, "observed": True}
    return {"time": len(discoveries), "observed": False}


def _number(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise RQ1AnalysisError(f"{label} must be finite and non-negative")
    return float(value)


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _family_block_contrast(
    differences: Mapping[str, float],
    families: Mapping[str, str],
    *,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    if isinstance(samples, bool) or not isinstance(samples, int) or samples <= 0:
        raise RQ1AnalysisError("bootstrap_samples must be a positive integer")
    family_members: dict[str, list[str]] = defaultdict(list)
    for skill in sorted(differences):
        family_members[families[skill]].append(skill)
    family_names = sorted(family_members)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(samples):
        sampled: list[float] = []
        for family in rng.choices(family_names, k=len(family_names)):
            members = family_members[family]
            for skill in rng.choices(members, k=len(members)):
                sampled.append(differences[skill])
        estimates.append(fmean(sampled))
    estimate = fmean(differences.values())
    return {
        "estimate": estimate,
        "ci95": [_percentile(estimates, 0.025), _percentile(estimates, 0.975)],
        "bootstrap_samples": samples,
        "bootstrap_seed": seed,
        "family_count": len(family_names),
        "skill_count": len(differences),
        "resampling_unit": "family-then-skill-paired-method-block",
    }


def _read_json(path: pathlib.Path, label: str) -> Any:
    if path.is_symlink():
        raise RQ1AnalysisError(f"{label} may not be a symlink")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RQ1AnalysisError(f"cannot read {label}: {path}") from error


def _within(root: pathlib.Path, raw: Any, label: str) -> pathlib.Path:
    try:
        return resolve_campaign_path(root, raw, label)
    except ValueError as error:
        raise RQ1AnalysisError(str(error)) from error


def _cluster_id(property_id: str, signature: str) -> str:
    return canonical_json_hash(
        {"property_id": property_id, "failure_signature": signature}
    )[:24]


def verify_rq1_cell(
    *,
    campaign_path: str | pathlib.Path,
    confirmation_path: str | pathlib.Path,
    repair_path: str | pathlib.Path,
    patch_confirmation_path: str | pathlib.Path | None = None,
    original_skill_dir: str | pathlib.Path,
    expected_method: str,
    expected_skill: str,
    family: str,
    contingency: str,
    allow_bounded_development: bool = False,
) -> dict[str, Any]:
    """Recursively verify and join one terminal campaign/confirmation/repair cell."""

    campaign_path = pathlib.Path(campaign_path).resolve()
    root = campaign_path.parent
    campaign = _read_json(campaign_path, "RQ1 campaign")
    if not isinstance(campaign, Mapping) or campaign.get("schema") != "campaign/2":
        raise RQ1AnalysisError("RQ1 analysis refuses legacy raw-property-only campaigns")
    if expected_method not in METHODS or campaign.get("method") != expected_method:
        raise RQ1AnalysisError("RQ1 campaign method identity mismatch")
    if campaign.get("skill") != expected_skill:
        raise RQ1AnalysisError("RQ1 campaign skill identity mismatch")
    embedded = campaign.get("protocol")
    if (
        not isinstance(embedded, Mapping)
        or canonical_json_hash(embedded) != campaign.get("protocol_hash")
    ):
        raise RQ1AnalysisError("RQ1 campaign embedded protocol hash mismatch")
    budget = campaign.get("budget")
    bounded = (
        allow_bounded_development
        and isinstance(budget, int)
        and not isinstance(budget, bool)
        and 0 < budget < 30
        and embedded.get("status") in {"runtime", "development-only"}
        and embedded.get("budget") == budget
    )
    expected_bootstrap = (
        0
        if expected_method == "random"
        else int(embedded.get("bootstrap_count", 0))
    )
    allocation = {
        "budget": budget,
        "bootstrap": expected_bootstrap,
        "exploration": budget - expected_bootstrap,
    }
    if (
        (not bounded and budget != 30)
        or campaign.get("counted_executions") != budget
        or campaign.get("complete") is not True
        or campaign.get("status") != "completed"
        or campaign.get("allocation") != allocation
        or campaign.get("agent_model") != campaign.get("model")
    ):
        raise RQ1AnalysisError("RQ1 campaign is not a complete frozen 30-run cell")
    attempts = campaign.get("attempts")
    if not isinstance(attempts, list):
        raise RQ1AnalysisError("RQ1 campaign attempts are malformed")
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            raise RQ1AnalysisError("RQ1 campaign attempt must be an object")
        attempt_id = attempt.get("attempt_id")
        if not isinstance(attempt_id, str) or not re.fullmatch(
            r"e[0-9]{4}-a[0-9]{2}", attempt_id
        ):
            raise RQ1AnalysisError("RQ1 campaign attempt ID is malformed")
        directory = root / "attempts" / attempt_id
        if directory.is_symlink() or not directory.is_dir():
            raise RQ1AnalysisError("RQ1 immutable attempt artifact directory is missing")
        linked = {
            "proposal": ("proposal.json", "proposal_hash"),
            "receipt": ("receipt.json", "receipt_hash"),
            "cleanup intent": ("cleanup.intent.json", "cleanup_intent_hash"),
            "cleanup": ("cleanup.json", "cleanup_hash"),
        }
        if attempt.get("consume_budget") is True:
            linked["fold"] = ("fold.json", "fold_hash")
        loaded: dict[str, Any] = {}
        for label, (filename, hash_field) in linked.items():
            value = _read_json(directory / filename, f"campaign {label}")
            if not isinstance(value, Mapping):
                raise RQ1AnalysisError(f"campaign {label} must be an object")
            if canonical_json_hash(value) != attempt.get(hash_field):
                raise RQ1AnalysisError(
                    f"campaign {label} hash mismatch for {attempt_id}"
                )
            loaded[label] = value
        candidate = attempt.get("candidate_id")
        proposal_candidate = loaded["proposal"].get("candidate")
        if (
            not isinstance(proposal_candidate, Mapping)
            or proposal_candidate.get("candidate_id") != candidate
            or dict(proposal_candidate.get("provenance") or {})
            != dict(attempt.get("provenance") or {})
            or loaded["receipt"].get("candidate_id") != candidate
            or dict(loaded["receipt"].get("result") or {})
            != dict(attempt.get("result") or {})
        ):
            raise RQ1AnalysisError(
                f"campaign proposal/receipt identity mismatch for {attempt_id}"
            )
        if attempt.get("consume_budget") is True and (
            loaded["proposal"].get("phase") != attempt.get("phase")
            or loaded["fold"].get("phase") != attempt.get("phase")
        ):
            raise RQ1AnalysisError(f"campaign fold phase mismatch for {attempt_id}")
    counted = [
        attempt
        for attempt in attempts
        if isinstance(attempt, Mapping) and attempt.get("consume_budget") is True
    ]
    if len(counted) != budget:
        raise RQ1AnalysisError("RQ1 campaign counted-run total differs from its budget")
    expected_executions = [f"e{ordinal:04d}" for ordinal in range(budget)]
    if [attempt.get("execution_id") for attempt in counted] != expected_executions:
        raise RQ1AnalysisError("RQ1 counted execution IDs are not contiguous")
    if len({attempt.get("attempt_id") for attempt in attempts}) != len(attempts):
        raise RQ1AnalysisError("RQ1 campaign has duplicate attempt IDs")

    search_cost = 0.0
    input_tokens = output_tokens = 0
    classifications: Counter[str] = Counter()
    targeting: Counter[str] = Counter()
    oracle_statuses: Counter[str] = Counter()
    generation_statuses: Counter[str] = Counter()
    infrastructure_statuses: Counter[str] = Counter()
    raw_failure_observations = 0
    runs_with_violation = 0
    fallbacks = 0
    raw_run_links: list[dict[str, Any]] = []
    for attempt in attempts:
        generation = attempt.get("generation_status")
        infrastructure = attempt.get("infrastructure_status")
        if isinstance(generation, str) and generation:
            generation_statuses[generation] += 1
        if isinstance(infrastructure, str) and infrastructure:
            infrastructure_statuses[infrastructure] += 1
    for ordinal, attempt in enumerate(counted):
        candidate = attempt.get("candidate_id")
        if not isinstance(candidate, str) or not candidate:
            raise RQ1AnalysisError("counted execution lacks a candidate identity")
        case = _within(root, attempt.get("case"), "case path")
        run = _within(
            root,
            attempt.get("run")
            or (attempt.get("result") or {}).get("run_dir"),
            "run path",
        )
        if not case.is_dir() or not run.is_dir():
            raise RQ1AnalysisError("counted execution case/run directory is missing")
        run_manifest = _read_json(run / "run.json", "agent run manifest")
        cost = _read_json(run / "cost.json", "agent run cost")
        verdicts = _read_json(run / "verdicts.json", "property verdict receipt")
        if not isinstance(run_manifest, Mapping) or not isinstance(cost, Mapping):
            raise RQ1AnalysisError("raw run/cost receipt must be an object")
        if not isinstance(verdicts, list) or any(
            not isinstance(verdict, Mapping) for verdict in verdicts
        ):
            raise RQ1AnalysisError("property verdict receipt must be a list of objects")
        result = attempt.get("result")
        if not isinstance(result, Mapping):
            raise RQ1AnalysisError("counted execution lacks a terminal result")
        recorded_run_id = result.get("agent_id") or result.get("run_id")
        if allow_bounded_development and recorded_run_id is None:
            # Older development campaigns persisted the authoritative identity in
            # run.json but did not duplicate it into the campaign result.  Permit
            # that hash-checked raw receipt only in the explicit bounded mode;
            # headline inputs must retain the redundant cross-record check.
            recorded_run_id = run_manifest.get("run_id")
        if (
            attempt.get("agent_started") is not True
            or result.get("agent_started") is not True
            or run_manifest.get("agent_started") is not True
            or run_manifest.get("model") != campaign.get("model")
            or run_manifest.get("run_id") != recorded_run_id
        ):
            raise RQ1AnalysisError("raw agent identity/model evidence mismatch")
        inline = result.get("verdicts")
        if isinstance(inline, list) and inline != verdicts:
            raise RQ1AnalysisError("campaign result differs from verdict receipt")
        incoming = cost.get("in", cost.get("input_tokens", 0))
        outgoing = cost.get("out", cost.get("output_tokens", 0))
        if (
            isinstance(incoming, bool)
            or not isinstance(incoming, int)
            or incoming < 0
            or isinstance(outgoing, bool)
            or not isinstance(outgoing, int)
            or outgoing < 0
        ):
            raise RQ1AnalysisError("raw run token counts are malformed")
        input_tokens += incoming
        output_tokens += outgoing
        search_cost += _number(
            cost.get("cost_provider_credits", cost.get("provider_credits", cost.get("price_provider_credits", 0.0)))
            or 0.0,
            "agent run cost",
        )
        raw_failure_observations += sum(
            verdict.get("holds") is False and verdict.get("violated") is True
            for verdict in verdicts
        )
        if attempt.get("violated"):
            runs_with_violation += 1
        oracle = attempt.get("oracle_status") or result.get("oracle_status")
        if isinstance(oracle, str) and oracle:
            oracle_statuses[oracle] += 1
        provenance = attempt.get("provenance")
        if isinstance(provenance, Mapping) and provenance.get("source") == "skillrace-fallback":
            fallbacks += 1
        classification = attempt.get("classification")
        if isinstance(classification, str) and classification:
            classifications[classification] += 1
        relationships = result.get("discovery_relationships")
        if isinstance(relationships, list):
            for relationship in relationships:
                if isinstance(relationship, Mapping) and isinstance(
                    relationship.get("relationship"), str
                ):
                    targeting[relationship["relationship"]] += 1
        raw_run_links.append(
            {
                "execution_id": f"e{ordinal:04d}",
                "candidate_id": candidate,
                "run_id": run_manifest.get("run_id"),
                "run_file_hash": file_hash(run / "run.json"),
                "cost_file_hash": file_hash(run / "cost.json"),
                "verdict_file_hash": file_hash(run / "verdicts.json"),
            }
        )
        search_cost += _number(
            result.get("compile_cost_provider_credits", 0.0) or 0.0,
            "candidate compilation cost",
        )
    for state_name in ("generator_state", "bootstrap_generator_state"):
        state = campaign.get(state_name)
        if isinstance(state, Mapping):
            # SkillRACE owns its bootstrap generator inside its main snapshot, so
            # count only the main snapshot there, matching campaign cost derivation.
            if not (
                state_name == "bootstrap_generator_state"
                and isinstance(campaign.get("generator_state"), Mapping)
                and campaign["generator_state"].get("schema")
                == "skillrace-generator/1"
            ):
                search_cost += _number(
                    state.get("gen_cost_provider_credits", 0.0) or 0.0,
                    "test generation cost",
                )

    try:
        confirmations = validate_confirmation_ledger(
            confirmation_path, campaign_root=root
        )
        raw_repairs = _read_json(pathlib.Path(repair_path), "repair/patch ledger")
        historical_repairs = (
            isinstance(raw_repairs, Mapping)
            and raw_repairs.get("schema") == "skillrace-failure-repairs/1"
        )
        if historical_repairs:
            repairs = validate_repair_ledger(repair_path)
            patch_confirmations = None
        elif (
            isinstance(raw_repairs, Mapping)
            and raw_repairs.get("schema") == "skillrace-patch-only-ledger/1"
        ):
            repairs = dict(raw_repairs)
            if patch_confirmation_path is None:
                raise ValueError("patch-only analysis requires its confirmation ledger")
            patch_confirmations = _read_json(
                pathlib.Path(patch_confirmation_path), "patch confirmation ledger"
            )
            if (
                not isinstance(patch_confirmations, Mapping)
                or patch_confirmations.get("schema")
                != "skillrace-patch-confirmations/1"
                or patch_confirmations.get("patch_ledger_hash")
                != canonical_json_hash(repairs)
            ):
                raise ValueError("patch confirmation ledger identity mismatch")
        else:
            raise ValueError("unsupported repair/patch ledger schema")
        expected_repairs = select_failure_repairs(
            campaign,
            skill_name=expected_skill,
            original_skill_dir=original_skill_dir,
            campaign_root=root,
            output_root=pathlib.Path(repair_path).resolve().parent,
            phase="public",
        )
    except (OSError, TypeError, ValueError) as error:
        raise RQ1AnalysisError(str(error)) from error
    source_hash = canonical_json_hash(campaign)
    if (
        confirmations.get("source_campaign_hash") != source_hash
        or confirmations.get("method") != expected_method
    ):
        raise RQ1AnalysisError("confirmation ledger source/method mismatch")
    if (
        repairs.get("source_campaign_hash") != source_hash
        or repairs.get("method") != expected_method
        or repairs.get("skill_name") != expected_skill
        or repairs.get("original_skill_hash") != package_hash(original_skill_dir)
    ):
        raise RQ1AnalysisError("repair ledger source/method/skill mismatch")
    expected_repair_ids = [request.repair_id for request in expected_repairs]
    repair_entries = repairs["repairs"] if historical_repairs else repairs["patches"]
    if [link.get("repair_id") for link in repair_entries] != expected_repair_ids:
        raise RQ1AnalysisError(
            "failed public executions must have exactly one repair each"
        )

    expected_clusters: dict[str, dict[str, Any]] = {}
    for request in expected_repairs:
        for property_id, signature in zip(
            request.failed_property_ids, request.failure_signatures, strict=True
        ):
            cluster = _cluster_id(property_id, signature)
            expected_clusters.setdefault(
                cluster,
                {
                    "execution_id": request.execution_id,
                    "attempt_id": request.attempt_id,
                    "property_id": property_id,
                    "failure_signature": signature,
                },
            )
    actual_clusters = confirmations.get("clusters")
    if not isinstance(actual_clusters, list) or {
        link.get("cluster_id") for link in actual_clusters
    } != set(expected_clusters):
        raise RQ1AnalysisError(
            "confirmation ledger is not exactly one job per suspected failure group"
        )
    reproduced_events: list[dict[str, Any]] = []
    confirmed_events: list[dict[str, Any]] = []
    confirmation_statuses: Counter[str] = Counter()
    repair_validation_statuses: Counter[str] = Counter()
    if historical_repairs:
        repair_status_by_attempt = {
            str(link.get("attempt_id")): str(link.get("status"))
            for link in repair_entries
        }
        repair_statuses = [str(link.get("status")) for link in repair_entries]
        repair_cost = _number(
            repairs.get("costs", {}).get("total_provider_credits"), "repair cost"
        )
    else:
        confirmation_rows = patch_confirmations.get("confirmations")
        if (
            not isinstance(confirmation_rows, list)
            or [row.get("repair_id") for row in confirmation_rows]
            != expected_repair_ids
        ):
            raise RQ1AnalysisError(
                "patch confirmations must cover every failed execution in order"
            )
        repair_status_by_attempt = {
            str(row.get("attempt_id")): str(row.get("status"))
            for row in confirmation_rows
        }
        repair_statuses = [str(row.get("status")) for row in confirmation_rows]
        repair_cost = _number(
            repairs.get("cost_provider_credits", 0.0), "patch cost"
        ) + _number(
            patch_confirmations.get("cost_provider_credits", 0.0),
            "patch confirmation cost",
        )
    for link in actual_clusters:
        cluster = link["cluster_id"]
        expected = expected_clusters[cluster]
        if (
            link.get("representative_execution_id") != expected["execution_id"]
            or link.get("representative_attempt_id") != expected["attempt_id"]
            or link.get("property_id") != expected["property_id"]
            or link.get("failure_signature") != expected["failure_signature"]
        ):
            raise RQ1AnalysisError("confirmation representative is not the first finding")
        status = link.get("status")
        confirmation_statuses[str(status)] += 1
        if status == "confirmed":
            match = re.fullmatch(r"e([0-9]{4})", expected["execution_id"])
            if match is None:
                raise RQ1AnalysisError("confirmed execution identity is malformed")
            event = {"execution": int(match.group(1)) + 1, "cluster_id": cluster}
            reproduced_events.append(event)
            if repair_status_by_attempt.get(expected["attempt_id"]) in {
                "repaired",
                "repair_confirmed",
            }:
                confirmed_events.append(event)
                repair_validation_statuses["repair-validated"] += 1
            else:
                repair_validation_statuses["reproduced-but-not-repaired"] += 1
        else:
            repair_validation_statuses["not-reproduced"] += 1
    reproduced_events.sort(key=lambda row: (row["execution"], row["cluster_id"]))
    confirmed_events.sort(key=lambda row: (row["execution"], row["cluster_id"]))
    confirmation_cost = _number(
        confirmations.get("costs", {}).get("total_provider_credits"), "confirmation cost"
    )
    search_cost = round(search_cost, 12)
    return {
        "schema": "skillrace-rq1-verified-cell/1",
        "method": expected_method,
        "skill": expected_skill,
        "family": family,
        "contingency": contingency,
        "budget": budget,
        "reproduced_events": reproduced_events,
        "confirmed_events": confirmed_events,
        "raw_failed_executions": len(expected_repairs),
        "raw_failure_observations": raw_failure_observations,
        "runs_with_violation": runs_with_violation,
        "repair_statuses": repair_statuses,
        "confirmation_executions": confirmations["confirmation_executions"],
        "confirmation_statuses": dict(sorted(confirmation_statuses.items())),
        "repair_validation_statuses": dict(
            sorted(repair_validation_statuses.items())
        ),
        "classifications": dict(sorted(classifications.items())),
        "targeting": dict(sorted(targeting.items())),
        "oracle_statuses": dict(sorted(oracle_statuses.items())),
        "candidate_accounting": {
            "attempts": len(attempts),
            "counted": len(counted),
            "pre_agent_rejected": len(attempts) - len(counted),
            "fallbacks": fallbacks,
            "generation_statuses": dict(sorted(generation_statuses.items())),
            "infrastructure_statuses": dict(
                sorted(infrastructure_statuses.items())
            ),
        },
        "tokens": {"input": input_tokens, "output": output_tokens},
        "costs": {
            "search_provider_credits": search_cost,
            "confirmation_provider_credits": confirmation_cost,
            "repair_provider_credits": repair_cost,
            "inclusive_provider_credits": round(search_cost + confirmation_cost + repair_cost, 12),
        },
        "source_artifacts": {
            "campaign_file_hash": file_hash(campaign_path),
            "campaign_artifact_hash": source_hash,
            "confirmation_file_hash": file_hash(confirmation_path),
            "repair_file_hash": file_hash(repair_path),
            "patch_confirmation_file_hash": (
                file_hash(patch_confirmation_path)
                if patch_confirmation_path is not None
                else None
            ),
            "raw_runs": raw_run_links,
        },
    }


def verify_rq1_experiment(
    *,
    experiment_manifest_path: str | pathlib.Path,
    schedule_path: str | pathlib.Path,
    d1_manifest_path: str | pathlib.Path,
    require_frozen: bool = True,
) -> list[dict[str, Any]]:
    """Verify the exact paired D1 schedule and every linked terminal cell."""

    manifest_path = pathlib.Path(experiment_manifest_path).resolve()
    schedule_path = pathlib.Path(schedule_path).resolve()
    d1_path = pathlib.Path(d1_manifest_path).resolve()
    manifest = _read_json(manifest_path, "RQ1 experiment manifest")
    schedule = _read_json(schedule_path, "RQ1 experiment schedule")
    d1 = _read_json(d1_path, "D1 manifest")
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("schema") != "skillrace-experiment-manifest/1"
    ):
        raise RQ1AnalysisError("unsupported RQ1 experiment manifest")
    if manifest.get("confirmation") != {"enabled": True} or not isinstance(
        manifest.get("repair"), Mapping
    ) or manifest["repair"].get("enabled") is not True:
        raise RQ1AnalysisError(
            "headline RQ1 requires enabled confirmation and repair post-search phases"
        )
    if (
        manifest["repair"].get("evidence_max_bytes")
        != RQ1_REPAIR_EVIDENCE_MAX_BYTES
    ):
        raise RQ1AnalysisError(
            "headline repair evidence budget must be exactly "
            f"{RQ1_REPAIR_EVIDENCE_MAX_BYTES} bytes"
        )
    if (
        not isinstance(schedule, Mapping)
        or schedule.get("schema") != "skillrace-experiment-schedule/1"
        or schedule.get("status") != "completed"
        or schedule.get("manifest_hash") != canonical_json_hash(manifest)
        or schedule.get("confirmation") != manifest.get("confirmation")
        or schedule.get("repair") != manifest.get("repair")
    ):
        raise RQ1AnalysisError("RQ1 schedule is incomplete or differs from its manifest")
    if not isinstance(d1, Mapping) or d1.get("schema") != "d1-suite/1":
        raise RQ1AnalysisError("unsupported D1 manifest")
    if require_frozen and d1.get("status") != "frozen":
        raise RQ1AnalysisError("headline analysis requires a frozen D1 manifest")
    headline = d1.get("headline_skills")
    if not isinstance(headline, list) or not headline:
        raise RQ1AnalysisError("D1 headline skill list is missing")
    metadata: dict[str, tuple[str, str]] = {}
    for raw in headline:
        if not isinstance(raw, Mapping):
            raise RQ1AnalysisError("D1 headline skill record is malformed")
        skill = raw.get("id")
        family = raw.get("family")
        contingency = raw.get("contingency")
        if (
            not isinstance(skill, str)
            or not skill
            or not isinstance(family, str)
            or not family
            or contingency not in {"low", "medium", "high"}
            or skill in metadata
        ):
            raise RQ1AnalysisError("D1 skill identity/family/contingency is malformed")
        metadata[skill] = (family, str(contingency))
    if require_frozen and len(metadata) != 30:
        raise RQ1AnalysisError("frozen headline D1 must contain exactly 30 skills")

    cells = manifest.get("cells")
    scheduled = schedule.get("cells")
    if not isinstance(cells, list) or not isinstance(scheduled, list):
        raise RQ1AnalysisError("RQ1 manifest/schedule cells are malformed")
    schedule_by_id: dict[str, Mapping[str, Any]] = {}
    for cell in scheduled:
        if (
            not isinstance(cell, Mapping)
            or not isinstance(cell.get("id"), str)
            or cell["id"] in schedule_by_id
        ):
            raise RQ1AnalysisError("RQ1 schedule has duplicate or malformed cell IDs")
        schedule_by_id[cell["id"]] = cell
    expected_pairs = {(method, skill) for method in METHODS for skill in metadata}
    seen_pairs: set[tuple[str, str]] = set()
    verified: list[dict[str, Any]] = []
    output_root = schedule_path.parent.resolve()
    for cell in cells:
        if not isinstance(cell, Mapping) or not isinstance(cell.get("campaign"), Mapping):
            raise RQ1AnalysisError("RQ1 experiment cell is malformed")
        identifier = cell.get("id")
        scheduled_cell = schedule_by_id.get(identifier)
        if scheduled_cell is None or scheduled_cell.get("status") != "completed":
            raise RQ1AnalysisError(f"RQ1 cell is missing or incomplete: {identifier}")
        arguments = cell["campaign"]
        method = arguments.get("method")
        skill = arguments.get("skill")
        pair = (method, skill)
        if pair not in expected_pairs or pair in seen_pairs:
            raise RQ1AnalysisError("RQ1 cells are not exact unique method/skill pairs")
        seen_pairs.add(pair)
        relative = cell.get("output")
        if not isinstance(relative, str) or not relative:
            raise RQ1AnalysisError("RQ1 cell output path is malformed")
        pure = pathlib.PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            raise RQ1AnalysisError("RQ1 cell output escapes the schedule root")
        output = output_root.joinpath(*pure.parts).resolve()
        if str(output) != scheduled_cell.get("output"):
            raise RQ1AnalysisError("RQ1 scheduled output differs from manifest output")
        skill_dir = pathlib.Path(str(arguments.get("skill_dir", ""))).resolve()
        family, contingency = metadata[str(skill)]
        patch_ledger = output / "repairs" / "patches.json"
        uses_patch_only = patch_ledger.is_file()
        verified.append(
            verify_rq1_cell(
                campaign_path=output / "campaign.json",
                confirmation_path=output / "confirmations" / "confirmation.json",
                repair_path=(
                    patch_ledger
                    if uses_patch_only
                    else output / "repairs" / "repairs.json"
                ),
                patch_confirmation_path=(
                    output / "repair-confirmations" / "confirmations.json"
                    if uses_patch_only
                    else None
                ),
                original_skill_dir=skill_dir,
                expected_method=str(method),
                expected_skill=str(skill),
                family=family,
                contingency=contingency,
            )
        )
    if seen_pairs != expected_pairs or set(schedule_by_id) != {
        str(cell.get("id")) for cell in cells
    }:
        raise RQ1AnalysisError("RQ1 requires exactly three paired cells for every D1 skill")
    return sorted(
        verified,
        key=lambda row: (row["skill"], METHODS.index(row["method"])),
    )


def _validated_cell(raw: Mapping[str, Any]) -> dict[str, Any]:
    method = raw.get("method")
    skill = raw.get("skill")
    family = raw.get("family")
    contingency = raw.get("contingency")
    budget = raw.get("budget")
    if method not in METHODS:
        raise RQ1AnalysisError("cell has a non-headline method")
    if not all(isinstance(value, str) and value for value in (skill, family)):
        raise RQ1AnalysisError("cell lacks skill/family identity")
    if contingency not in {"low", "medium", "high"}:
        raise RQ1AnalysisError("cell has invalid contingency")
    if budget != 30:
        raise RQ1AnalysisError("headline RQ1 cell must have exactly 30 executions")
    raw_events = raw.get("confirmed_events")
    if not isinstance(raw_events, list):
        raise RQ1AnalysisError("cell confirmed events are missing")
    events: list[tuple[int, str]] = []
    for event in raw_events:
        if not isinstance(event, Mapping):
            raise RQ1AnalysisError("confirmed event must be an object")
        events.append((event.get("execution"), event.get("cluster_id")))
    curve = discovery_curve(events, budget=budget)
    raw_statuses = raw.get("repair_statuses")
    if not isinstance(raw_statuses, list) or any(
        status not in REPAIR_STATUSES for status in raw_statuses
    ):
        raise RQ1AnalysisError("cell repair statuses are malformed")
    failed = raw.get("raw_failed_executions")
    if (
        isinstance(failed, bool)
        or not isinstance(failed, int)
        or failed < 0
        or failed != len(raw_statuses)
    ):
        raise RQ1AnalysisError("repair denominator differs from raw failed executions")
    confirmation_executions = raw.get("confirmation_executions")
    if (
        isinstance(confirmation_executions, bool)
        or not isinstance(confirmation_executions, int)
        or confirmation_executions < 0
    ):
        raise RQ1AnalysisError("confirmation execution count is malformed")
    costs = raw.get("costs")
    if not isinstance(costs, Mapping):
        raise RQ1AnalysisError("cell costs are missing")
    normalized_costs = {
        key: _number(costs.get(key), f"cell cost {key}")
        for key in ("search_provider_credits", "confirmation_provider_credits", "repair_provider_credits", "inclusive_provider_credits")
    }
    expected_total = sum(
        normalized_costs[key]
        for key in ("search_provider_credits", "confirmation_provider_credits", "repair_provider_credits")
    )
    if abs(normalized_costs["inclusive_provider_credits"] - expected_total) > 1e-9:
        raise RQ1AnalysisError("cell inclusive cost does not equal all execution phases")
    statuses = Counter(raw_statuses)
    discoveries = [False] * budget
    for execution, _ in events:
        discoveries[execution - 1] = True
    result = dict(raw)
    result.update(
        {
            "method": method,
            "skill": skill,
            "family": family,
            "contingency": contingency,
            "budget": budget,
            "confirmed_events": [
                {"execution": execution, "cluster_id": cluster}
                for execution, cluster in events
            ],
            "confirmed_distinct_defects": curve[-1],
            "confirmed_defect_yield": curve[-1] / budget,
            "discovery_curve": curve,
            "survival": survival_record(discoveries),
            "repair_status_counts": {
                status: statuses.get(status, 0) for status in REPAIR_STATUSES
            },
            "repair_executions": len(raw_statuses),
            "repair_rate": (
                statuses.get("repaired", 0) / len(raw_statuses)
                if raw_statuses
                else None
            ),
            "costs": normalized_costs,
        }
    )
    return result


def analyze_verified_cells(
    cells: Iterable[Mapping[str, Any]],
    *,
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 20260712,
) -> dict[str, Any]:
    """Analyze cells that have already passed recursive artifact verification."""

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    metadata: dict[str, tuple[str, str]] = {}
    for raw in cells:
        if not isinstance(raw, Mapping):
            raise RQ1AnalysisError("analysis cell must be an object")
        row = _validated_cell(raw)
        key = (row["method"], row["skill"])
        if key in seen:
            raise RQ1AnalysisError(f"duplicate method/skill cell: {key}")
        seen.add(key)
        current = (row["family"], row["contingency"])
        if row["skill"] in metadata and metadata[row["skill"]] != current:
            raise RQ1AnalysisError("skill metadata differs across paired methods")
        metadata[row["skill"]] = current
        rows.append(row)
    if not rows:
        raise RQ1AnalysisError("RQ1 analysis requires cells")
    skills = sorted(metadata)
    expected = {(method, skill) for method in METHODS for skill in skills}
    if seen != expected:
        raise RQ1AnalysisError("RQ1 requires exactly three paired methods per skill")

    by_skill: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_skill[row["skill"]][row["method"]] = row
    for skill in skills:
        normalizer = max(
            by_skill[skill][method]["confirmed_distinct_defects"] for method in METHODS
        )
        for method in METHODS:
            row = by_skill[skill][method]
            row["normalized_auc"] = normalized_auc(
                row["discovery_curve"], maximum_final_yield=normalizer
            )
            row["auc_all_zero"] = normalizer == 0
    rows = sorted(rows, key=lambda row: (row["skill"], METHODS.index(row["method"])))

    method_summaries: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        selected = [row for row in rows if row["method"] == method]
        statuses = Counter(
            status
            for row in selected
            for status in row["repair_statuses"]
        )
        repairs = sum(row["repair_executions"] for row in selected)
        defects = sum(row["confirmed_distinct_defects"] for row in selected)
        search = sum(row["budget"] for row in selected)
        class_counts: Counter[str] = Counter()
        target_counts: Counter[str] = Counter()
        oracle_counts: Counter[str] = Counter()
        generation_counts: Counter[str] = Counter()
        infrastructure_counts: Counter[str] = Counter()
        for row in selected:
            class_counts.update(row.get("classifications", {}))
            target_counts.update(row.get("targeting", {}))
            oracle_counts.update(row.get("oracle_statuses", {}))
            accounting = row.get("candidate_accounting", {})
            if isinstance(accounting, Mapping):
                generation_counts.update(accounting.get("generation_statuses", {}))
                infrastructure_counts.update(
                    accounting.get("infrastructure_statuses", {})
                )
        method_summaries[method] = {
            "skills": len(selected),
            "search_agent_executions": search,
            "confirmed_distinct_defects": defects,
            "confirmed_defect_yield": defects / search,
            "mean_normalized_auc": fmean(row["normalized_auc"] for row in selected),
            "confirmation_executions": sum(
                row["confirmation_executions"] for row in selected
            ),
            "runs_with_violation": sum(
                int(row.get("runs_with_violation", 0)) for row in selected
            ),
            "raw_failure_observations": sum(
                int(row.get("raw_failure_observations", 0)) for row in selected
            ),
            "repair_executions": repairs,
            **{status: statuses.get(status, 0) for status in REPAIR_STATUSES},
            "repair_rate": statuses.get("repaired", 0) / repairs if repairs else None,
            "classifications": dict(sorted(class_counts.items())),
            "targeting": dict(sorted(target_counts.items())),
            "oracle_statuses": dict(sorted(oracle_counts.items())),
            "candidate_accounting": {
                "attempts": sum(
                    int(row.get("candidate_accounting", {}).get("attempts", 0))
                    for row in selected
                ),
                "pre_agent_rejected": sum(
                    int(
                        row.get("candidate_accounting", {}).get(
                            "pre_agent_rejected", 0
                        )
                    )
                    for row in selected
                ),
                "fallbacks": sum(
                    int(row.get("candidate_accounting", {}).get("fallbacks", 0))
                    for row in selected
                ),
                "generation_statuses": dict(sorted(generation_counts.items())),
                "infrastructure_statuses": dict(
                    sorted(infrastructure_counts.items())
                ),
            },
            "costs": {
                key: round(sum(row["costs"][key] for row in selected), 12)
                for key in (
                    "search_provider_credits",
                    "confirmation_provider_credits",
                    "repair_provider_credits",
                    "inclusive_provider_credits",
                )
            },
        }

    yields = {
        skill: {
            method: by_skill[skill][method]["confirmed_defect_yield"]
            for method in METHODS
        }
        for skill in skills
    }
    families = {skill: metadata[skill][0] for skill in skills}
    contrasts = {}
    for offset, control in enumerate(("random", "greybox")):
        differences = {
            skill: yields[skill]["skillrace"] - yields[skill][control]
            for skill in skills
        }
        contrasts[f"skillrace_minus_{control}"] = _family_block_contrast(
            differences,
            families,
            samples=bootstrap_samples,
            seed=bootstrap_seed + offset,
        )
    return {
        "schema": "skillrace-rq1-analysis/1",
        "methods": list(METHODS),
        "skill_count": len(skills),
        "family_count": len(set(families.values())),
        "primary_metric": "distinct-confirmed-defects-per-search-agent-execution",
        "confirmation_counted_in_search_budget": False,
        "repair_counted_in_search_budget": False,
        "per_cell": rows,
        "by_method": method_summaries,
        "paired_contrasts": contrasts,
    }


def write_analysis_outputs(
    analysis: Mapping[str, Any], out_dir: str | pathlib.Path
) -> dict[str, pathlib.Path]:
    """Write deterministic JSON/CSV/TeX and plot-source CSV artifacts."""

    if analysis.get("schema") != "skillrace-rq1-analysis/1":
        raise RQ1AnalysisError("unsupported RQ1 analysis object")
    output = pathlib.Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": output / "rq1-summary.json",
        "csv": output / "rq1-campaigns.csv",
        "latex": output / "rq1-macros.tex",
        "plot_csv": output / "rq1-discovery.csv",
    }
    atomic_write_json(paths["json"], dict(analysis))

    rows = analysis.get("per_cell")
    if not isinstance(rows, list):
        raise RQ1AnalysisError("analysis per_cell rows are missing")
    cell_fields = [
        "skill",
        "family",
        "contingency",
        "method",
        "budget",
        "confirmed_distinct_defects",
        "confirmed_defect_yield",
        "normalized_auc",
        "raw_failed_executions",
        "confirmation_executions",
        "repair_executions",
        "repair_rate",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=cell_fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field) for field in cell_fields})
    atomic_write_text(paths["csv"], buffer.getvalue())

    plot = io.StringIO(newline="")
    plot_writer = csv.DictWriter(
        plot,
        fieldnames=("skill", "family", "method", "execution", "confirmed_defects"),
        lineterminator="\n",
    )
    plot_writer.writeheader()
    for row in rows:
        for execution, defects in enumerate(row["discovery_curve"], start=1):
            plot_writer.writerow(
                {
                    "skill": row["skill"],
                    "family": row["family"],
                    "method": row["method"],
                    "execution": execution,
                    "confirmed_defects": defects,
                }
            )
    atomic_write_text(paths["plot_csv"], plot.getvalue())

    summaries = analysis["by_method"]
    lines = ["% Generated by skillrace.analyze_rq1; do not edit."]
    labels = {"random": "Random", "greybox": "Greybox", "skillrace": "SkillRACE"}
    for method in METHODS:
        label = labels[method]
        value = summaries[method]["confirmed_defect_yield"]
        lines.append(
            f"\\newcommand{{\\{label}ConfirmedYield}}{{{value:.6f}}}"
        )
    atomic_write_text(paths["latex"], "\n".join(lines) + "\n")
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify RQ1 artifacts and generate paper-owned tables/plot data"
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--schedule", required=True)
    parser.add_argument("--d1", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260712)
    parser.add_argument("--allow-draft", action="store_true")
    args = parser.parse_args(argv)
    cells = verify_rq1_experiment(
        experiment_manifest_path=args.manifest,
        schedule_path=args.schedule,
        d1_manifest_path=args.d1,
        require_frozen=not args.allow_draft,
    )
    analysis = analyze_verified_cells(
        cells,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.bootstrap_seed,
    )
    paths = write_analysis_outputs(analysis, args.out)
    print(f"verified {analysis['skill_count']} skills; wrote {paths['json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
