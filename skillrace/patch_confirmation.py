"""Independent exact replay of an immutable completed patch receipt."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Mapping
from typing import Any

from .io_utils import atomic_write_json, canonical_json_hash, file_hash
from .repair_validation import FailureRepairRequest, _classify_replay
from .repair_validation import select_failure_repairs
from .revise_skill import package_hash, validate_skill_package


def _read(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {label}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def confirm_patched_execution(
    request: FailureRepairRequest,
    *,
    patch_dir: str | pathlib.Path,
    output_dir: str | pathlib.Path,
    executor,
) -> dict[str, Any]:
    """Replay the saved case once; this interface has no patch backend parameter."""

    patch_root = pathlib.Path(patch_dir).resolve()
    output = pathlib.Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    terminal = output / "confirmation.json"
    if terminal.exists():
        result = _read(terminal, "patch confirmation")
        receipt = _read(output / "receipt.json", "patch confirmation receipt")
        if receipt.get("result_hash") != canonical_json_hash(result):
            raise ValueError("patch confirmation receipt mismatch")
        return result
    patch = _read(patch_root / "patch.json", "completed patch")
    receipt = _read(patch_root / "receipt.json", "completed patch receipt")
    if (
        patch.get("schema") != "skillrace-patch-only-result/1"
        or patch.get("repair_id") != request.repair_id
        or patch.get("status") != "completed"
        or receipt.get("patch_hash") != canonical_json_hash(patch)
        or receipt.get("intent_hash") != file_hash(patch_root / "intent.json")
    ):
        raise ValueError("confirmation requires an immutable completed patch receipt")
    skill = validate_skill_package(patch_root / "skill")
    if package_hash(skill) != patch.get("patched_skill_hash"):
        raise ValueError("completed patch package hash mismatch")
    intent = {
        "schema": "skillrace-patch-confirmation-intent/1",
        "repair_id": request.repair_id,
        "patch_hash": canonical_json_hash(patch),
        "case_dir": str(request.case_dir.resolve()),
    }
    atomic_write_json(output / "intent.json", intent)
    replay_dir = output / "replay"
    replay_dir.mkdir(exist_ok=True)
    try:
        raw = executor(request, skill, replay_dir)
    except Exception as error:
        raw = {"status": "error", "error": type(error).__name__}
    raw = dict(raw) if isinstance(raw, Mapping) else {"status": "error"}
    old_status, verdicts = _classify_replay(raw, request)
    status = "repair_confirmed" if old_status == "repaired" else old_status
    result = {
        "schema": "skillrace-patch-confirmation-result/1",
        "repair_id": request.repair_id,
        "status": status,
        "patch_hash": canonical_json_hash(patch),
        "patched_skill_hash": patch["patched_skill_hash"],
        "verdicts": verdicts,
        "input_tokens": int(raw.get("input_tokens", 0) or 0),
        "output_tokens": int(raw.get("output_tokens", 0) or 0),
        "cost_provider_credits": float(raw.get("cost_provider_credits", 0.0) or 0.0),
        "wall_seconds": float(raw.get("wall_seconds", 0.0) or 0.0),
    }
    atomic_write_json(terminal, result)
    atomic_write_json(
        output / "receipt.json",
        {
            "schema": "skillrace-patch-confirmation-receipt/1",
            "repair_id": request.repair_id,
            "intent_hash": file_hash(output / "intent.json"),
            "result_hash": canonical_json_hash(result),
        },
    )
    return result


def confirm_campaign_patches(
    campaign: Mapping[str, Any],
    patch_ledger: Mapping[str, Any],
    *,
    skill_name: str,
    original_skill_dir: str | pathlib.Path,
    campaign_root: str | pathlib.Path,
    patch_root: str | pathlib.Path,
    output_root: str | pathlib.Path,
    executor,
) -> dict[str, Any]:
    """Independently replay every completed per-failure patch exactly once."""

    if (
        patch_ledger.get("schema") != "skillrace-patch-only-ledger/1"
        or patch_ledger.get("source_campaign_hash") != canonical_json_hash(campaign)
    ):
        raise ValueError("patch confirmation ledger/campaign mismatch")
    requests = select_failure_repairs(
        campaign,
        skill_name=skill_name,
        original_skill_dir=original_skill_dir,
        campaign_root=campaign_root,
        output_root=patch_root,
        phase="public",
    )
    links = patch_ledger.get("patches")
    if not isinstance(links, list) or [row.get("repair_id") for row in links] != [
        request.repair_id for request in requests
    ]:
        raise ValueError("patch confirmation requires exact per-failure coverage")
    output = pathlib.Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    ledger_path = output / "confirmations.json"
    if ledger_path.exists():
        return _read(ledger_path, "patch confirmation ledger")
    rows = []
    total_cost = 0.0
    for request, link in zip(requests, links, strict=True):
        if link.get("status") == "completed":
            result = confirm_patched_execution(
                request,
                patch_dir=pathlib.Path(patch_root) / request.repair_id,
                output_dir=output / request.repair_id,
                executor=executor,
            )
            total_cost += float(result["cost_provider_credits"])
            status = result["status"]
        else:
            status = "patch_not_completed"
        rows.append(
            {
                "repair_id": request.repair_id,
                "execution_id": request.execution_id,
                "attempt_id": request.attempt_id,
                "status": status,
            }
        )
    ledger = {
        "schema": "skillrace-patch-confirmations/1",
        "method": campaign.get("method"),
        "source_campaign_hash": canonical_json_hash(campaign),
        "patch_ledger_hash": canonical_json_hash(patch_ledger),
        "failed_public_executions": len(requests),
        "confirmation_executions": sum(row["status"] != "patch_not_completed" for row in rows),
        "confirmed_defects": sum(row["status"] == "repair_confirmed" for row in rows),
        "confirmations": rows,
        "cost_provider_credits": round(total_cost, 12),
    }
    atomic_write_json(ledger_path, ledger)
    return ledger
