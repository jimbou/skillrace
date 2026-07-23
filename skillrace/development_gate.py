"""Verify a bounded development campaign exercised every post-search phase."""

from __future__ import annotations

import argparse
import json
import pathlib
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .io_utils import atomic_write_json, canonical_json_hash
from .repair_validation import validate_repair_ledger
from .rq3_confirmation import validate_confirmation_ledger


class DevelopmentGateError(ValueError):
    """A bounded development artifact is incomplete or internally inconsistent."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DevelopmentGateError(f"{label} must be an object")
    return value


def build_development_gate_report(
    campaign: Mapping[str, Any],
    repairs: Mapping[str, Any],
    confirmations: Mapping[str, Any],
) -> dict[str, Any]:
    """Join already-loaded campaign, repair, and confirmation evidence."""

    campaign = _mapping(campaign, "campaign")
    repairs = _mapping(repairs, "repair ledger")
    confirmations = _mapping(confirmations, "confirmation ledger")
    search_count = campaign.get("counted_executions")
    protocol = campaign.get("protocol")
    if (
        campaign.get("schema") != "campaign/2"
        or campaign.get("complete") is not True
        or campaign.get("status") != "completed"
        or isinstance(search_count, bool)
        or not isinstance(search_count, int)
        or not 0 < search_count < 30
        or not isinstance(protocol, Mapping)
        or protocol.get("status") not in {"runtime", "development-only"}
    ):
        raise DevelopmentGateError(
            "development gate requires a complete bounded campaign with a development protocol"
        )
    attempts = campaign.get("attempts")
    if not isinstance(attempts, list):
        raise DevelopmentGateError("campaign attempts are missing")
    counted = [
        attempt
        for attempt in attempts
        if isinstance(attempt, Mapping) and attempt.get("consume_budget") is True
    ]
    if len(counted) != search_count:
        raise DevelopmentGateError("counted campaign attempt accounting is inconsistent")
    for attempt in counted:
        result = attempt.get("result")
        has_inline_verdicts = isinstance(result, Mapping) and isinstance(
            result.get("verdicts"), list
        )
        has_linked_verdict_receipt = (
            isinstance(result, Mapping)
            and result.get("oracle_status") == "completed"
            and isinstance(result.get("n_verdicts"), int)
            and not isinstance(result.get("n_verdicts"), bool)
            and result["n_verdicts"] > 0
        )
        if (
            attempt.get("agent_started") is not True
            or not isinstance(result, Mapping)
            or result.get("agent_started") is not True
            or not (has_inline_verdicts or has_linked_verdict_receipt)
        ):
            raise DevelopmentGateError(
                "counted execution lacks proposal/agent/checker terminal evidence"
            )
    failed = [attempt for attempt in counted if attempt.get("violated")]
    if not failed:
        raise DevelopmentGateError("development gate requires at least one raw failed execution")

    source_hash = canonical_json_hash(campaign)
    if (
        repairs.get("source_campaign_hash") != source_hash
        or confirmations.get("source_campaign_hash") != source_hash
    ):
        raise DevelopmentGateError("post-search source campaign hash mismatch")
    if (
        repairs.get("schema") != "skillrace-failure-repairs/1"
        or repairs.get("search_agent_executions") != search_count
        or repairs.get("failed_public_executions") != len(failed)
        or not isinstance(repairs.get("repairs"), list)
        or repairs.get("repair_executions") != len(repairs["repairs"])
        or repairs.get("repair_executions", 0) < 1
    ):
        raise DevelopmentGateError(
            "development gate requires at least one repair and exact replay for every failure"
        )
    failed_attempts = {str(attempt.get("attempt_id")) for attempt in failed}
    repair_by_attempt: dict[str, Mapping[str, Any]] = {}
    for repair in repairs["repairs"]:
        repair = _mapping(repair, "repair entry")
        attempt_id = repair.get("attempt_id")
        if not isinstance(attempt_id, str) or attempt_id in repair_by_attempt:
            raise DevelopmentGateError("repair attempt identities are missing or duplicated")
        repair_by_attempt[attempt_id] = repair
    if set(repair_by_attempt) != failed_attempts:
        raise DevelopmentGateError("repair ledger does not cover every raw failed execution")

    clusters = confirmations.get("clusters")
    if (
        confirmations.get("schema") != "skillrace-confirmations/1"
        or confirmations.get("development_only") is not True
        or confirmations.get("search_agent_executions") != search_count
        or not isinstance(clusters, list)
        or confirmations.get("confirmation_executions") != len(clusters)
        or len(clusters) < 1
    ):
        raise DevelopmentGateError(
            "development gate requires at least one confirmation execution"
        )
    joined = []
    for raw_cluster in clusters:
        cluster = _mapping(raw_cluster, "confirmation cluster")
        attempt_id = cluster.get("representative_attempt_id")
        if not isinstance(attempt_id, str) or attempt_id not in repair_by_attempt:
            raise DevelopmentGateError(
                "confirmation representative lacks its per-failure repair replay"
            )
        joined.append(
            {
                "cluster_id": cluster.get("cluster_id"),
                "property_id": cluster.get("property_id"),
                "representative_execution_id": cluster.get(
                    "representative_execution_id"
                ),
                "representative_attempt_id": attempt_id,
                "confirmation_status": cluster.get("status"),
                "repair_status": repair_by_attempt[attempt_id].get("status"),
            }
        )
    joined.sort(key=lambda row: (str(row["representative_attempt_id"]), str(row["cluster_id"])))
    repair_statuses = Counter(
        str(repair.get("status")) for repair in repairs["repairs"]
    )
    confirmation_statuses = Counter(str(cluster.get("status")) for cluster in clusters)
    repair_validated = sum(
        row["confirmation_status"] == "confirmed"
        and row["repair_status"] == "repaired"
        for row in joined
    )
    return {
        "schema": "skillrace-bounded-development-gate/1",
        "status": "passed",
        "development_only": True,
        "source_campaign_hash": source_hash,
        "method": campaign.get("method"),
        "skill": campaign.get("skill"),
        "model": campaign.get("model"),
        "search_agent_executions": search_count,
        "raw_failed_executions": len(failed),
        "repair_executions": repairs["repair_executions"],
        "confirmation_executions": confirmations["confirmation_executions"],
        "repair_statuses": dict(sorted(repair_statuses.items())),
        "confirmation_statuses": dict(sorted(confirmation_statuses.items())),
        "repair_validated_reproduced_clusters": repair_validated,
        "clusters": joined,
        "costs": {
            "repair_provider_credits": float(
                _mapping(repairs.get("costs", {}), "repair costs").get(
                    "total_provider_credits", 0.0
                )
            ),
            "confirmation_provider_credits": float(
                _mapping(confirmations.get("costs", {}), "confirmation costs").get(
                    "total_provider_credits", 0.0
                )
            ),
        },
        "phase_coverage": {
            "proposal_agent_checker": True,
            "patch_exact_replay": True,
            "unchanged_skill_confirmation": True,
            "analysis": True,
        },
    }


def write_development_gate_report(
    report: Mapping[str, Any], out_dir: str | pathlib.Path
) -> pathlib.Path:
    if report.get("schema") != "skillrace-bounded-development-gate/1":
        raise DevelopmentGateError("unsupported development gate report")
    path = pathlib.Path(out_dir) / "development-gate.json"
    atomic_write_json(path, dict(report))
    return path


def analyze_development_gate(
    *,
    campaign_path: str | pathlib.Path,
    repair_path: str | pathlib.Path,
    confirmation_path: str | pathlib.Path,
    out_dir: str | pathlib.Path,
) -> dict[str, Any]:
    campaign_path = pathlib.Path(campaign_path).resolve()
    try:
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DevelopmentGateError("cannot read development campaign") from error
    repairs = validate_repair_ledger(repair_path)
    confirmations = validate_confirmation_ledger(
        confirmation_path, campaign_root=campaign_path.parent
    )
    report = build_development_gate_report(campaign, repairs, confirmations)
    write_development_gate_report(report, out_dir)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--repairs", required=True)
    parser.add_argument("--confirmations", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report = analyze_development_gate(
        campaign_path=args.campaign,
        repair_path=args.repairs,
        confirmation_path=args.confirmations,
        out_dir=args.out,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
