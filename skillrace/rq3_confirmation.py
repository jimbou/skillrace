"""Frozen, exactly-once confirmation reruns for deduplicated RQ3 findings."""

from __future__ import annotations

import dataclasses
import json
import math
import pathlib
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .io_utils import (
    atomic_write_json,
    canonical_json_hash,
    file_hash,
    resolve_campaign_path,
)


CONFIRMATION_SCHEMA = "skillrace-confirmations/1"
_TERMINAL_STATUSES = {"confirmed", "not-reproduced", "error", "timeout", "inconclusive"}


@dataclasses.dataclass(frozen=True)
class ConfirmationRequest:
    """One representative rerun, deliberately separate from the search budget."""

    cluster_id: str
    property_id: str
    failure_signature: str
    failure_summary: str
    representative_execution_id: str
    representative_attempt_id: str
    representative_candidate_id: str
    case: str
    run_dir: pathlib.Path


def _uncertain_error(message: str) -> RuntimeError:
    # Import lazily to avoid an rq3 -> confirmation -> rq3 import cycle.
    from .rq3 import UncertainExternalOutcomeError

    return UncertainExternalOutcomeError(message)


def _read_object(path: pathlib.Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"{label} symlink is forbidden: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _safe_number(value: Any, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"{label} must be finite and non-negative")
    return float(value)


def _normalized_failure_detail(value: Any) -> str:
    text = " ".join(str(value or "unspecified failure").lower().split())
    text = re.sub(r"0x[0-9a-f]+", "<hex>", text)
    text = re.sub(r"(?:/[A-Za-z0-9_.-]+){2,}", "<path>", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "<number>", text)
    return text[:500]


def failure_signature(verdict: Mapping[str, Any]) -> str:
    """Mechanically cluster one property failure while removing volatile literals."""

    property_id = verdict.get("property_id")
    if not isinstance(property_id, str) or not property_id:
        raise ValueError("failure verdict requires property_id")
    return canonical_json_hash(
        {
            "property_id": property_id,
            "normalized_detail": _normalized_failure_detail(
                verdict.get("detail") or verdict.get("failure_summary")
            ),
        }
    )


def _suspected_clusters(
    campaign: Mapping[str, Any], campaign_root: pathlib.Path | None
) -> list[dict[str, Any]]:
    attempts = campaign.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError("campaign attempts are missing")
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for fallback, raw_attempt in enumerate(attempts):
        if not isinstance(raw_attempt, Mapping) or raw_attempt.get("consume_budget") is not True:
            continue
        result = raw_attempt.get("result")
        result = result if isinstance(result, Mapping) else {}
        signatures = result.get("failure_signatures")
        signatures = signatures if isinstance(signatures, Mapping) else {}
        verdicts = result.get("verdicts")
        verdicts = verdicts if isinstance(verdicts, list) else []
        evidence_link = None
        if not verdicts and campaign_root is not None:
            run = raw_attempt.get("run") or result.get("run_dir")
            if isinstance(run, str) and run:
                run_path = resolve_campaign_path(
                    campaign_root, run, "verdict path"
                )
                verdict_path = run_path / "verdicts.json"
                if verdict_path.is_file() and not verdict_path.is_symlink():
                    try:
                        loaded_verdicts = json.loads(
                            verdict_path.read_text(encoding="utf-8")
                        )
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise ValueError("campaign verdict receipt is malformed") from error
                    if not isinstance(loaded_verdicts, list):
                        raise ValueError("campaign verdict receipt must be a list")
                    verdicts = loaded_verdicts
                    evidence_link = {
                        "path": verdict_path.relative_to(campaign_root).as_posix(),
                        "file_hash": file_hash(verdict_path),
                    }
        verdict_by_property = {
            row.get("property_id"): row
            for row in verdicts
            if isinstance(row, Mapping) and row.get("violated") is True
        }
        violated = raw_attempt.get("violated")
        if not isinstance(violated, list):
            continue
        for property_id in violated:
            if not isinstance(property_id, str) or not property_id:
                continue
            signature = signatures.get(property_id)
            verdict = verdict_by_property.get(property_id)
            if signature is None and verdict is not None:
                signature = failure_signature(verdict)
            if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
                raise ValueError(
                    f"counted violation {raw_attempt.get('attempt_id')}/{property_id} "
                    "lacks a mechanical failure signature"
                )
            key = (property_id, signature)
            if key in rows:
                continue
            execution_id = raw_attempt.get("execution_id") or f"e{fallback:04d}"
            attempt_id = raw_attempt.get("attempt_id") or f"{execution_id}-a00"
            candidate_id = raw_attempt.get("candidate_id") or "unknown"
            case = raw_attempt.get("case") or result.get("case_dir") or ""
            if not all(isinstance(item, str) and item for item in (execution_id, attempt_id, candidate_id, case)):
                raise ValueError("suspected finding lacks replayable representative identity")
            summary = (
                str(verdict.get("detail") or verdict.get("failure_summary"))
                if isinstance(verdict, Mapping)
                else f"{property_id} violated"
            )
            provenance = raw_attempt.get("provenance")
            provenance = provenance if isinstance(provenance, Mapping) else {}
            task_summary = str(provenance.get("task_nl") or "").strip()
            environment_summary = str(provenance.get("env_nl") or "").strip()
            if not task_summary or not environment_summary:
                raise ValueError(
                    "suspected finding lacks representative task/environment provenance"
                )
            rows[key] = {
                "cluster_id": canonical_json_hash(
                    {"property_id": property_id, "failure_signature": signature}
                )[:24],
                "property_id": property_id,
                "failure_signature": signature,
                "failure_summary": summary[:500],
                "task_summary": task_summary[:500],
                "environment_summary": environment_summary[:500],
                "representative_execution_id": execution_id,
                "representative_attempt_id": attempt_id,
                "representative_candidate_id": candidate_id,
                "case": case,
                "case_hash": canonical_json_hash(
                    {"candidate_id": candidate_id, "case": case}
                ),
                "failure_evidence": evidence_link,
            }
    return sorted(rows.values(), key=lambda row: (row["representative_execution_id"], row["cluster_id"]))


def _normalize_result(raw: Any, request: ConfirmationRequest) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raw = {"status": "error", "error": "confirmation executor returned no object"}
    status = raw.get("status")
    if status not in {"completed", "timeout", "error", "inconclusive"}:
        status = "error"
    verdicts = raw.get("verdicts")
    verdicts = [dict(row) for row in verdicts if isinstance(row, Mapping)] if isinstance(verdicts, list) else []
    reproduced = False
    for verdict in verdicts:
        if (
            verdict.get("property_id") == request.property_id
            and verdict.get("violated") is True
            and failure_signature(verdict) == request.failure_signature
        ):
            reproduced = True
            break
    if status == "completed":
        terminal = "confirmed" if reproduced else "not-reproduced"
    else:
        terminal = status
    return {
        "schema": "skillrace-confirmation-result/1",
        "cluster_id": request.cluster_id,
        "status": terminal,
        "reproduced": reproduced,
        "verdicts": verdicts,
        "agent_id": raw.get("agent_id") or raw.get("run_id"),
        "input_tokens": int(raw.get("input_tokens", 0) or 0),
        "output_tokens": int(raw.get("output_tokens", 0) or 0),
        "cost_provider_credits": _safe_number(raw.get("cost_provider_credits", 0.0) or 0.0, "confirmation cost"),
        "wall_seconds": _safe_number(raw.get("wall_seconds", 0.0) or 0.0, "confirmation wall time"),
        "error": str(raw.get("error") or raw.get("error_message") or "")[:500],
    }


def _validate_cluster_files(root: pathlib.Path, link: Mapping[str, Any]) -> dict[str, Any]:
    cluster_id = link.get("cluster_id")
    if not isinstance(cluster_id, str) or not re.fullmatch(r"[0-9a-f]{24}", cluster_id):
        raise ValueError("confirmation cluster ID is malformed")
    directory = root / "clusters" / cluster_id
    start_path = directory / "start.json"
    result_path = directory / "result.json"
    receipt_path = directory / "receipt.json"
    start = _read_object(start_path, "confirmation start")
    result = _read_object(result_path, "confirmation result")
    receipt = _read_object(receipt_path, "confirmation receipt")
    if file_hash(start_path) != link.get("start_hash"):
        raise ValueError(f"confirmation start hash mismatch for {cluster_id}")
    if file_hash(result_path) != link.get("result_hash"):
        raise ValueError(f"confirmation result hash mismatch for {cluster_id}")
    if file_hash(receipt_path) != link.get("receipt_hash"):
        raise ValueError(f"confirmation receipt hash mismatch for {cluster_id}")
    if (
        start.get("schema") != "skillrace-confirmation-start/1"
        or start.get("cluster_id") != cluster_id
        or result.get("schema") != "skillrace-confirmation-result/1"
        or result.get("cluster_id") != cluster_id
        or receipt
        != {
            "schema": "skillrace-confirmation-receipt/1",
            "cluster_id": cluster_id,
            "start_hash": link["start_hash"],
            "result_hash": link["result_hash"],
        }
    ):
        raise ValueError(f"confirmation artifact identity mismatch for {cluster_id}")
    if result.get("status") != link.get("status") or result.get("status") not in _TERMINAL_STATUSES:
        raise ValueError(f"confirmation terminal status mismatch for {cluster_id}")
    return result


def validate_confirmation_ledger(
    path: str | pathlib.Path,
    *,
    campaign_root: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    path = pathlib.Path(path)
    ledger = _read_object(path, "confirmation ledger")
    if ledger.get("schema") != CONFIRMATION_SCHEMA:
        raise ValueError("unsupported confirmation ledger")
    clusters = ledger.get("clusters")
    if not isinstance(clusters, list):
        raise ValueError("confirmation ledger clusters are malformed")
    results = [_validate_cluster_files(path.parent, link) for link in clusters]
    if campaign_root is not None:
        source_root = pathlib.Path(campaign_root).resolve()
        for link in clusters:
            evidence = link.get("failure_evidence")
            if evidence is None:
                continue
            if not isinstance(evidence, Mapping):
                raise ValueError("confirmation failure evidence is malformed")
            relative = pathlib.PurePosixPath(str(evidence.get("path", "")))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("confirmation failure evidence path is unsafe")
            target = (source_root / pathlib.Path(*relative.parts)).resolve()
            if source_root not in target.parents or file_hash(target) != evidence.get("file_hash"):
                raise ValueError("confirmation failure evidence hash mismatch")
    if ledger.get("confirmation_executions") != len(clusters):
        raise ValueError("confirmation execution count mismatch")
    for cluster in clusters:
        if not all(
            isinstance(cluster.get(field), str) and cluster[field].strip()
            for field in ("task_summary", "environment_summary")
        ):
            raise ValueError(
                "confirmation cluster lacks representative task/environment summary"
            )
    if ledger.get("confirmation_executions_counted_in_search_budget") is not False:
        raise ValueError("confirmation executions must remain outside the search budget")
    total = round(sum(_safe_number(row.get("cost_provider_credits", 0.0), "confirmation cost") for row in results), 6)
    if ledger.get("costs") != {
        "total_provider_credits": total,
        "input_tokens": sum(int(row.get("input_tokens", 0)) for row in results),
        "output_tokens": sum(int(row.get("output_tokens", 0)) for row in results),
        "wall_seconds": round(sum(float(row.get("wall_seconds", 0.0)) for row in results), 3),
    }:
        raise ValueError("confirmation cost accounting mismatch")
    return ledger


def confirm_campaign_findings(
    campaign: Mapping[str, Any],
    out_dir: str | pathlib.Path,
    *,
    executor: Callable[[ConfirmationRequest], Mapping[str, Any]],
    campaign_root: str | pathlib.Path | None = None,
    allow_bounded_development: bool = False,
) -> dict[str, Any]:
    """Confirm one representative per property/signature exactly once."""

    if not isinstance(campaign, Mapping):
        raise ValueError("campaign must be an object")
    search_executions = campaign.get("counted_executions")
    bounded_development = (
        allow_bounded_development
        and campaign.get("complete") is True
        and campaign.get("status") == "completed"
        and isinstance(search_executions, int)
        and not isinstance(search_executions, bool)
        and 0 < search_executions < 30
    )
    if bounded_development:
        protocol = campaign.get("protocol")
        if not isinstance(protocol, Mapping) or protocol.get("status") not in {
            "runtime",
            "development-only",
        }:
            raise ValueError(
                "bounded confirmation requires an embedded development protocol"
            )
    elif campaign.get("complete") is not True or search_executions != 30:
        raise ValueError("confirmation requires a complete 30-execution campaign")
    output = pathlib.Path(out_dir)
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise ValueError("confirmation output must be a regular directory")
    output.mkdir(parents=True, exist_ok=True)
    ledger_path = output / "confirmation.json"
    source_hash = canonical_json_hash(campaign)
    source_root = pathlib.Path(campaign_root).resolve() if campaign_root is not None else None
    if ledger_path.exists():
        ledger = validate_confirmation_ledger(ledger_path, campaign_root=source_root)
        if ledger.get("source_campaign_hash") != source_hash:
            raise ValueError("confirmation ledger source campaign hash mismatch")
        return ledger

    links: list[dict[str, Any]] = []
    for cluster in _suspected_clusters(campaign, source_root):
        cluster_id = cluster["cluster_id"]
        directory = output / "clusters" / cluster_id
        directory.mkdir(parents=True, exist_ok=True)
        start_path = directory / "start.json"
        result_path = directory / "result.json"
        receipt_path = directory / "receipt.json"
        start = {
            "schema": "skillrace-confirmation-start/1",
            "source_campaign_hash": source_hash,
            **cluster,
        }
        if start_path.exists():
            if _read_object(start_path, "confirmation start") != start:
                raise ValueError(f"confirmation start identity mismatch for {cluster_id}")
            if not result_path.exists():
                raise _uncertain_error(
                    f"confirmation outcome is unknown for {cluster_id}; durable start "
                    "exists without a terminal result"
                )
        else:
            atomic_write_json(start_path, start)

        request = ConfirmationRequest(
            cluster_id=cluster_id,
            property_id=cluster["property_id"],
            failure_signature=cluster["failure_signature"],
            failure_summary=cluster["failure_summary"],
            representative_execution_id=cluster["representative_execution_id"],
            representative_attempt_id=cluster["representative_attempt_id"],
            representative_candidate_id=cluster["representative_candidate_id"],
            case=cluster["case"],
            run_dir=directory / "agent",
        )
        if result_path.exists():
            result = _read_object(result_path, "confirmation result")
        else:
            try:
                raw = executor(request)
            except TimeoutError as error:
                raw = {"status": "timeout", "error": str(error)}
            except Exception as error:  # durable terminal error; BaseException stays unknown
                raw = {"status": "error", "error": f"{type(error).__name__}: {error}"}
            result = _normalize_result(raw, request)
            atomic_write_json(result_path, result)
        start_hash = file_hash(start_path)
        result_hash = file_hash(result_path)
        receipt = {
            "schema": "skillrace-confirmation-receipt/1",
            "cluster_id": cluster_id,
            "start_hash": start_hash,
            "result_hash": result_hash,
        }
        if receipt_path.exists():
            if _read_object(receipt_path, "confirmation receipt") != receipt:
                raise ValueError(f"confirmation receipt mismatch for {cluster_id}")
        else:
            atomic_write_json(receipt_path, receipt)
        links.append(
            {
                **cluster,
                "status": result["status"],
                "reproduction_count": int(result.get("reproduced") is True),
                "agent_id": result.get("agent_id"),
                "start_hash": start_hash,
                "result_hash": result_hash,
                "receipt_hash": file_hash(receipt_path),
            }
        )

    results = [
        _read_object(output / "clusters" / link["cluster_id"] / "result.json", "confirmation result")
        for link in links
    ]
    ledger = {
        "schema": CONFIRMATION_SCHEMA,
        "source_campaign_hash": source_hash,
        "method": campaign.get("method"),
        "protocol_hash": campaign.get("protocol_hash"),
        "base_skill_hash": campaign.get("base_skill_hash"),
        "search_agent_executions": search_executions,
        "confirmation_executions": len(links),
        "confirmation_executions_counted_in_search_budget": False,
        "clusters": links,
        "costs": {
            "total_provider_credits": round(sum(float(row["cost_provider_credits"]) for row in results), 6),
            "input_tokens": sum(int(row["input_tokens"]) for row in results),
            "output_tokens": sum(int(row["output_tokens"]) for row in results),
            "wall_seconds": round(sum(float(row["wall_seconds"]) for row in results), 3),
        },
    }
    if bounded_development:
        ledger["development_only"] = True
    atomic_write_json(ledger_path, ledger)
    return validate_confirmation_ledger(ledger_path, campaign_root=source_root)
