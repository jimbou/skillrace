"""Method-neutral, deterministic feedback envelopes for the lean RQ3 study.

The projector accepts the campaign record produced by any of the three headline
methods.  It deliberately does not expose the producer name to the reviser and
never upgrades an ordinary violation into a confirmed defect: confirmation must
be explicit in a campaign record or in its recorded reproduction rerun.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .io_utils import canonical_json_bytes, canonical_json_hash


FEEDBACK_SCHEMA = "skillrace-feedback/1"
PRODUCERS = ("random", "greybox", "skillrace")
BYTE_BUDGET_ID = "canonical-json-utf8-bytes/1"
DEFAULT_LIMITS = {
    "max_string_chars": 320,
    "max_confirmed_findings": 40,
    "max_explored_situations": 30,
    "max_tool_novelty": 20,
    "max_guard_mutations": 20,
    "max_branch_outcomes": 20,
    "max_inconclusive_findings": 30,
}
_TOP_LEVEL_KEYS = (
    "schema",
    "confirmed_findings",
    "explored_situations",
    "method_evidence",
    "inconclusive_findings",
    "costs",
    "truncation",
    "accounting",
)
_SECTIONS = (
    "confirmed_findings",
    "explored_situations",
    "tool_novelty",
    "guard_mutations",
    "branch_outcomes",
    "inconclusive_findings",
)


class FeedbackEnvelopeError(ValueError):
    """Raised when campaign feedback cannot satisfy the frozen envelope contract."""


def envelope_byte_count(value: Mapping[str, Any]) -> int:
    """Return the exact canonical-JSON UTF-8 size used by the frozen envelope cap."""

    return len(canonical_json_bytes(value))


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text[:limit]


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return default


def _attempts(campaign: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = campaign.get("attempts")
    if not isinstance(raw, list) or not raw:
        raw = campaign.get("iterations", [])
    return [row for row in raw if isinstance(row, Mapping)]


def _ordinal(row: Mapping[str, Any], fallback: int) -> int:
    value = row.get("i", fallback)
    return value + 1 if isinstance(value, int) and value >= 0 else fallback + 1


def _record_key(row: Mapping[str, Any]) -> tuple[int, str, str]:
    ordinal = row.get("execution_ordinal", 2**31 - 1)
    identifier = str(
        row.get("finding_id")
        or row.get("candidate_id")
        or row.get("attempt_id")
        or ""
    )
    return (ordinal if isinstance(ordinal, int) else 2**31 - 1, identifier, canonical_json_hash(row))


def _confirmation_rows(
    confirmations: Mapping[str, Any] | None, string_limit: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(confirmations, Mapping):
        return rows
    clusters = confirmations.get("clusters")
    if not isinstance(clusters, list):
        raise FeedbackEnvelopeError("confirmation ledger clusters are malformed")
    for fallback, finding in enumerate(clusters):
        if (
            not isinstance(finding, Mapping)
            or finding.get("status") != "confirmed"
            or finding.get("reproduction_count") != 1
        ):
            continue
        property_id = finding.get("property_id")
        signature = finding.get("failure_signature")
        if (
            not isinstance(property_id, str)
            or not property_id
            or not isinstance(signature, str)
        ):
            raise FeedbackEnvelopeError("confirmed cluster lacks property/signature")
        execution_id = finding.get("representative_execution_id")
        match = re.fullmatch(r"e([0-9]{4})", str(execution_id))
        ordinal = int(match.group(1)) + 1 if match else fallback + 1
        rows.append(
            {
                "finding_id": _clip(
                    finding.get("cluster_id") or canonical_json_hash(
                        {"property_id": property_id, "failure_signature": signature}
                    )[:20],
                    128,
                ),
                "execution_ordinal": ordinal,
                "property_id": _clip(property_id, 128),
                "failure_summary": _clip(finding.get("failure_summary"), string_limit),
                "task_summary": _clip(finding.get("task_summary"), string_limit),
                "environment_summary": _clip(
                    finding.get("environment_summary"), string_limit
                ),
                "reproduction_count": 1,
                "replay_pointer": {
                    "candidate_id": _clip(
                        finding.get("representative_candidate_id"), 128
                    ),
                    "case_hash": _clip(finding.get("case_hash"), 64),
                },
            }
        )

    deduplicated: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=_record_key):
        prior = deduplicated.get(row["finding_id"])
        if prior is None or row["reproduction_count"] > prior["reproduction_count"]:
            deduplicated[row["finding_id"]] = row
    return sorted(deduplicated.values(), key=_record_key)


def _explored_rows(
    attempts: Iterable[Mapping[str, Any]], string_limit: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fallback, attempt in enumerate(attempts):
        if attempt.get("consume_budget") is not True:
            continue
        provenance = attempt.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        rows.append(
            {
                "execution_ordinal": _ordinal(attempt, fallback),
                "candidate_id": _clip(
                    attempt.get("candidate_id") or attempt.get("attempt_id"), 128
                ),
                "task_summary": _clip(provenance.get("task_nl"), string_limit),
                "environment_summary": _clip(provenance.get("env_nl"), string_limit),
                "runner_status": _clip(attempt.get("runner_status"), 64),
                "oracle_status": _clip(attempt.get("oracle_status"), 64),
            }
        )
    return sorted(rows, key=_record_key)


def _inconclusive_rows(
    attempts: Iterable[Mapping[str, Any]], string_limit: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fallback, attempt in enumerate(attempts):
        raw = attempt.get("inconclusive", [])
        if not isinstance(raw, list):
            continue
        provenance = attempt.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        for property_id in raw:
            if not isinstance(property_id, str):
                continue
            rows.append(
                {
                    "finding_id": canonical_json_hash(
                        {
                            "attempt": attempt.get("attempt_id"),
                            "property_id": property_id,
                        }
                    )[:20],
                    "execution_ordinal": _ordinal(attempt, fallback),
                    "property_id": _clip(property_id, 128),
                    "summary": _clip(
                        attempt.get("inconclusive_summary")
                        or "The recorded oracle could not determine this property",
                        string_limit,
                    ),
                    "task_summary": _clip(provenance.get("task_nl"), string_limit),
                    "candidate_id": _clip(
                        attempt.get("candidate_id") or attempt.get("attempt_id"), 128
                    ),
                }
            )
    return sorted(rows, key=_record_key)


def _method_evidence_rows(
    attempts: Iterable[Mapping[str, Any]], generator_state: Any, string_limit: int
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        "tool_novelty": [],
        "guard_mutations": [],
        "branch_outcomes": [],
    }
    state = generator_state if isinstance(generator_state, Mapping) else {}
    novelty = state.get("novelty")
    if isinstance(novelty, Mapping):
        for kind, values in sorted(novelty.items(), key=lambda item: str(item[0])):
            if isinstance(values, (list, tuple, set)):
                for value in sorted(str(item) for item in values):
                    result["tool_novelty"].append(
                        {"kind": _clip(kind, 64), "value": _clip(value, string_limit)}
                    )
            elif isinstance(values, (str, int, float)) and not isinstance(values, bool):
                result["tool_novelty"].append(
                    {"kind": _clip(kind, 64), "value": _clip(values, string_limit)}
                )
    for fallback, attempt in enumerate(attempts):
        provenance = attempt.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        if provenance.get("mutation") or provenance.get("guard"):
            result["guard_mutations"].append(
                {
                    "execution_ordinal": _ordinal(attempt, fallback),
                    "guard_summary": _clip(provenance.get("guard"), string_limit),
                    "mutation_summary": _clip(provenance.get("mutation"), string_limit),
                    "targeted_property": _clip(
                        provenance.get("targeted_property"), 128
                    ),
                }
            )
        classification = attempt.get("classification")
        if classification:
            if isinstance(classification, Mapping):
                outcome = classification.get("branch_outcome") or classification.get("outcome")
                targeting = classification.get("targeting")
            else:
                outcome, targeting = classification, None
            result["branch_outcomes"].append(
                {
                    "execution_ordinal": _ordinal(attempt, fallback),
                    "outcome": _clip(outcome, 96),
                    "targeting": _clip(targeting, 96),
                }
            )
    for rows in result.values():
        rows.sort(key=_record_key)
    return result


def _costs(
    campaign: Mapping[str, Any],
    attempts: list[Mapping[str, Any]],
    confirmations: Mapping[str, Any] | None,
) -> dict[str, Any]:
    counted = [row for row in attempts if row.get("consume_budget") is True]
    explicit = campaign.get("costs")
    explicit = explicit if isinstance(explicit, Mapping) else {}
    model_cost = _number(explicit.get("model_cost_usd"))
    model_cost += sum(_number(row.get("compile_cost_usd")) for row in attempts)
    state = campaign.get("generator_state")
    if isinstance(state, Mapping):
        model_cost += _number(state.get("gen_cost_usd"))
    confirmation_costs = confirmations.get("costs") if isinstance(confirmations, Mapping) else {}
    confirmation_costs = confirmation_costs if isinstance(confirmation_costs, Mapping) else {}
    return {
        "attempts": len(attempts),
        "counted_agent_executions": len(counted),
        "confirmation_executions": int(
            confirmations.get("confirmation_executions", 0)
            if isinstance(confirmations, Mapping)
            else 0
        ),
        "confirmation_cost_usd": round(_number(confirmation_costs.get("total_usd")), 6),
        "model_cost_usd": round(model_cost, 6),
        "agent_cost_usd": round(_number(explicit.get("agent_cost_usd")), 6),
        "wall_seconds": round(
            _number(explicit.get("wall_seconds"))
            or sum(_number(row.get("seconds")) for row in attempts),
            3,
        ),
    }


def _empty_envelope(
    campaign: Mapping[str, Any],
    confirmations: Mapping[str, Any] | None,
    max_bytes: int,
    limits: Mapping[str, int],
    totals: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "schema": FEEDBACK_SCHEMA,
        "confirmed_findings": [],
        "explored_situations": [],
        "method_evidence": {
            "tool_novelty": [],
            "guard_mutations": [],
            "branch_outcomes": [],
        },
        "inconclusive_findings": [],
        "costs": {},
        "truncation": {
            "input": dict(totals),
            "included": {name: 0 for name in _SECTIONS},
            "dropped": dict(totals),
        },
        "accounting": {
            "budget_unit": BYTE_BUDGET_ID,
            "max_bytes": max_bytes,
            "used_bytes": 0,
            "source_campaign_hash": canonical_json_hash(campaign),
            "source_confirmation_hash": (
                canonical_json_hash(confirmations)
                if isinstance(confirmations, Mapping)
                else None
            ),
            "limits": dict(limits),
        },
    }


def _synchronize_used_bytes(envelope: dict[str, Any]) -> int:
    for _ in range(16):
        count = envelope_byte_count(envelope)
        if envelope["accounting"]["used_bytes"] == count:
            return count
        envelope["accounting"]["used_bytes"] = count
    raise FeedbackEnvelopeError("used-byte accounting did not stabilize")


def _target(envelope: dict[str, Any], section: str) -> list[Any]:
    if section in envelope["method_evidence"]:
        return envelope["method_evidence"][section]
    return envelope[section]


def build_feedback_envelope(
    campaign: Mapping[str, Any],
    max_bytes: int = 24000,
    *,
    limits: Mapping[str, int] | None = None,
    confirmations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one campaign into the shared ordered, bounded feedback schema."""

    if not isinstance(campaign, Mapping):
        raise FeedbackEnvelopeError("campaign must be an object")
    if campaign.get("method") not in PRODUCERS:
        raise FeedbackEnvelopeError("campaign producer must be random, greybox, or skillrace")
    if confirmations is not None:
        if not isinstance(confirmations, Mapping):
            raise FeedbackEnvelopeError("confirmations must be an object")
        if confirmations.get("source_campaign_hash") != canonical_json_hash(campaign):
            raise FeedbackEnvelopeError("confirmation/campaign hash mismatch")
        if confirmations.get("confirmation_executions_counted_in_search_budget") is not False:
            raise FeedbackEnvelopeError("confirmations must remain outside the search budget")
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise FeedbackEnvelopeError("max_bytes must be a positive integer")
    selected_limits = dict(DEFAULT_LIMITS)
    if limits is not None:
        if set(limits) != set(DEFAULT_LIMITS) or any(
            not isinstance(value, int) or value < 0 for value in limits.values()
        ):
            raise FeedbackEnvelopeError("limits must contain the frozen non-negative fields")
        selected_limits = dict(limits)
    attempts = _attempts(campaign)
    string_limit = selected_limits["max_string_chars"]
    candidates = {
        "confirmed_findings": _confirmation_rows(confirmations, string_limit),
        "explored_situations": _explored_rows(attempts, string_limit),
        **_method_evidence_rows(
            attempts, campaign.get("generator_state"), string_limit
        ),
        "inconclusive_findings": _inconclusive_rows(attempts, string_limit),
    }
    section_limits = {
        "confirmed_findings": selected_limits["max_confirmed_findings"],
        "explored_situations": selected_limits["max_explored_situations"],
        "tool_novelty": selected_limits["max_tool_novelty"],
        "guard_mutations": selected_limits["max_guard_mutations"],
        "branch_outcomes": selected_limits["max_branch_outcomes"],
        "inconclusive_findings": selected_limits["max_inconclusive_findings"],
    }
    totals = {name: len(candidates[name]) for name in _SECTIONS}
    envelope = _empty_envelope(campaign, confirmations, max_bytes, selected_limits, totals)
    envelope["costs"] = _costs(campaign, attempts, confirmations)
    _synchronize_used_bytes(envelope)
    if envelope_byte_count(envelope) > max_bytes:
        raise FeedbackEnvelopeError(
            f"max_bytes={max_bytes} is too small for the fixed feedback schema"
        )
    # Deterministic round-robin allocation prevents an early, verbose section from
    # consuming the complete byte budget before method-specific evidence is considered.
    # This policy is identical for all producers; empty sections simply take no turn.
    indexes = {section: 0 for section in _SECTIONS}
    blocked: set[str] = set()
    while True:
        progressed = False
        for section in _SECTIONS:
            limit = min(section_limits[section], len(candidates[section]))
            index = indexes[section]
            if section in blocked or index >= limit:
                continue
            before = copy.deepcopy(envelope)
            _target(envelope, section).append(candidates[section][index])
            envelope["truncation"]["included"][section] += 1
            envelope["truncation"]["dropped"][section] -= 1
            _synchronize_used_bytes(envelope)
            if envelope_byte_count(envelope) > max_bytes:
                envelope = before
                blocked.add(section)
                continue
            indexes[section] += 1
            progressed = True
        if not progressed:
            break
    _synchronize_used_bytes(envelope)
    validate_feedback_envelope(envelope)
    return envelope


def validate_feedback_envelope(envelope: Mapping[str, Any]) -> None:
    """Validate schema, ordering, equal limits, and exact accounting metadata."""

    if not isinstance(envelope, Mapping) or tuple(envelope) != _TOP_LEVEL_KEYS:
        raise FeedbackEnvelopeError("feedback envelope has the wrong ordered schema")
    if envelope.get("schema") != FEEDBACK_SCHEMA:
        raise FeedbackEnvelopeError("unsupported feedback envelope schema")
    evidence = envelope.get("method_evidence")
    if not isinstance(evidence, Mapping) or tuple(evidence) != (
        "tool_novelty",
        "guard_mutations",
        "branch_outcomes",
    ):
        raise FeedbackEnvelopeError("method_evidence has the wrong ordered schema")
    for section in _SECTIONS:
        if not isinstance(_target(dict(envelope), section), list):
            raise FeedbackEnvelopeError(f"{section} must be a list")
    accounting = envelope.get("accounting")
    if not isinstance(accounting, Mapping) or accounting.get("budget_unit") != BYTE_BUDGET_ID:
        raise FeedbackEnvelopeError("wrong accounting byte-budget unit")
    # Custom limits are supported only when they are recorded completely; all
    # methods in one comparison must pass the same mapping to the projector.
    limits = accounting.get("limits")
    if (
        not isinstance(limits, Mapping)
        or set(limits) != set(DEFAULT_LIMITS)
        or any(not isinstance(value, int) or value < 0 for value in limits.values())
    ):
        raise FeedbackEnvelopeError("wrong accounting limits")
    section_limit_names = {
        "confirmed_findings": "max_confirmed_findings",
        "explored_situations": "max_explored_situations",
        "tool_novelty": "max_tool_novelty",
        "guard_mutations": "max_guard_mutations",
        "branch_outcomes": "max_branch_outcomes",
        "inconclusive_findings": "max_inconclusive_findings",
    }
    for section, limit_name in section_limit_names.items():
        if len(_target(dict(envelope), section)) > limits[limit_name]:
            raise FeedbackEnvelopeError(f"{section} exceeds its recorded item limit")

    def validate_strings(value: Any) -> None:
        if isinstance(value, str) and len(value) > limits["max_string_chars"]:
            raise FeedbackEnvelopeError("feedback value exceeds the recorded string field limit")
        if isinstance(value, Mapping):
            for item in value.values():
                validate_strings(item)
        elif isinstance(value, list):
            for item in value:
                validate_strings(item)

    for section in _SECTIONS:
        validate_strings(_target(dict(envelope), section))
    for finding in envelope["confirmed_findings"]:
        if not isinstance(finding, Mapping) or not isinstance(
            finding.get("reproduction_count"), int
        ) or finding["reproduction_count"] <= 0:
            raise FeedbackEnvelopeError("confirmed findings require explicit reproduction")
    truncation = envelope.get("truncation")
    if not isinstance(truncation, Mapping) or set(truncation) != {
        "input",
        "included",
        "dropped",
    }:
        raise FeedbackEnvelopeError("truncation metadata is malformed")
    for section in _SECTIONS:
        source_count = truncation["input"].get(section)
        included = truncation["included"].get(section)
        dropped = truncation["dropped"].get(section)
        if (
            not all(isinstance(value, int) and value >= 0 for value in (source_count, included, dropped))
            or included + dropped != source_count
            or included != len(_target(dict(envelope), section))
        ):
            raise FeedbackEnvelopeError(f"truncation metadata mismatch for {section}")
    if not isinstance(accounting.get("source_campaign_hash"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", accounting["source_campaign_hash"]
    ):
        raise FeedbackEnvelopeError("source_campaign_hash must be a SHA-256 digest")
    expected = envelope_byte_count(envelope)
    if accounting.get("used_bytes") != expected:
        raise FeedbackEnvelopeError(
            f"used_bytes mismatch: recorded={accounting.get('used_bytes')}, actual={expected}"
        )
    maximum = accounting.get("max_bytes")
    if not isinstance(maximum, int) or expected > maximum:
        raise FeedbackEnvelopeError("feedback envelope exceeds max_bytes")
    if "method" in envelope:
        raise FeedbackEnvelopeError("producer identity must not enter revision feedback")
    confirmation_hash = accounting.get("source_confirmation_hash")
    if confirmation_hash is not None and not (
        isinstance(confirmation_hash, str)
        and re.fullmatch(r"[0-9a-f]{64}", confirmation_hash)
    ):
        raise FeedbackEnvelopeError("source_confirmation_hash must be null or SHA-256")
