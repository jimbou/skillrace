"""Crash-safe patch generation that never executes or confirms a patched skill."""

from __future__ import annotations

import difflib
import pathlib
import shutil
import time
from collections.abc import Mapping
from typing import Any

from .io_utils import (
    atomic_write_json,
    atomic_write_text,
    canonical_json_hash,
    file_hash,
)
from .repair_validation import (
    FailureRepairRequest,
    build_repair_evidence,
    select_failure_repairs,
)
from .revise_skill import CAMPAIGN_ONLY_FILES, package_hash, validate_skill_package


PATCH_STATUSES = ("completed", "timeout", "error", "invalid_patch", "outcome_unknown")


def _read_object(path: pathlib.Path, label: str) -> dict[str, Any]:
    import json

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {label}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _write_or_verify(path: pathlib.Path, value: Mapping[str, Any], label: str) -> bool:
    frozen = dict(value)
    if path.exists():
        if _read_object(path, label) != frozen:
            raise ValueError(f"{label} identity mismatch")
        return False
    atomic_write_json(path, frozen)
    return True


def _validate_evidence(request: FailureRepairRequest, evidence: Mapping[str, Any]) -> None:
    payload = evidence.get("reviser_payload")
    if (
        evidence.get("repair_id") not in (None, request.repair_id)
        or not isinstance(payload, Mapping)
        or evidence.get("evidence_hash") != canonical_json_hash(payload)
        or payload.get("original_skill_hash") != request.original_skill_hash
    ):
        raise ValueError("patch evidence identity mismatch")


def _files(root: pathlib.Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("patched skill may not contain symlinks")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _revision_safe_files(root: pathlib.Path) -> dict[str, bytes]:
    """Mirror the shared skill-copy boundary used by revision and patch backends."""

    ignored_roots = {"repo", "seeds", ".skillrace"}
    return {
        name: content
        for name, content in _files(root).items()
        if pathlib.PurePosixPath(name).parts[0] not in ignored_roots
        and pathlib.PurePosixPath(name).name not in CAMPAIGN_ONLY_FILES
        and not pathlib.PurePosixPath(name).name.endswith(".log")
    }


def _validate_only_skill_changed(original: pathlib.Path, patched: pathlib.Path) -> None:
    before = _revision_safe_files(original)
    after = _revision_safe_files(patched)
    if set(before) != set(after):
        raise ValueError("patched package changed its file set")
    changed = [name for name in before if before[name] != after[name]]
    if changed != ["SKILL.md"]:
        raise ValueError("patch must change only SKILL.md exactly once")


def _diff(original: pathlib.Path, patched: pathlib.Path) -> str:
    before = (original / "SKILL.md").read_text(encoding="utf-8").splitlines(True)
    after = (patched / "SKILL.md").read_text(encoding="utf-8").splitlines(True)
    return "".join(
        difflib.unified_diff(before, after, fromfile="original/SKILL.md", tofile="patched/SKILL.md")
    )


def _terminal_receipt(output: pathlib.Path, result: Mapping[str, Any]) -> dict[str, Any]:
    receipt = {
        "schema": "skillrace-patch-only-receipt/1",
        "repair_id": result["repair_id"],
        "intent_hash": file_hash(output / "intent.json"),
        "patch_hash": canonical_json_hash(result),
        "skill_hash": result.get("patched_skill_hash"),
        "diff_hash": file_hash(output / "skill.diff") if (output / "skill.diff").is_file() else None,
    }
    _write_or_verify(output / "receipt.json", receipt, "patch receipt")
    return receipt


def _load_terminal(output: pathlib.Path, request: FailureRepairRequest) -> dict[str, Any]:
    result = _read_object(output / "patch.json", "patch result")
    receipt = _read_object(output / "receipt.json", "patch receipt")
    if (
        result.get("schema") != "skillrace-patch-only-result/1"
        or result.get("repair_id") != request.repair_id
        or result.get("status") not in PATCH_STATUSES
        or receipt.get("patch_hash") != canonical_json_hash(result)
        or receipt.get("intent_hash") != file_hash(output / "intent.json")
    ):
        raise ValueError("patch-only terminal receipt is inconsistent")
    if result["status"] == "completed":
        skill = validate_skill_package(output / "skill")
        if package_hash(skill) != result.get("patched_skill_hash"):
            raise ValueError("completed patched skill hash mismatch")
    return result


def patch_failed_execution(
    request: FailureRepairRequest,
    evidence: Mapping[str, Any],
    *,
    backend,
) -> dict[str, Any]:
    """Produce one immutable patch and stop before any execution or checker call."""

    if not isinstance(request, FailureRepairRequest):
        raise TypeError("patch request must be FailureRepairRequest")
    if package_hash(request.original_skill_dir) != request.original_skill_hash:
        raise ValueError("original skill package differs from patch request")
    _validate_evidence(request, evidence)
    output = request.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "patch.json").exists():
        return _load_terminal(output, request)

    intent_path = output / "intent.json"
    if intent_path.exists():
        # A semantic call may have escaped before the process disappeared. Never
        # repeat it under this identity; materialize the uncertainty as terminal.
        intent = _read_object(intent_path, "patch intent")
        if intent.get("repair_id") != request.repair_id or intent.get("evidence_hash") != evidence["evidence_hash"]:
            raise ValueError("patch intent identity mismatch")
        result = {
            "schema": "skillrace-patch-only-result/1",
            "repair_id": request.repair_id,
            "method": request.method,
            "status": "outcome_unknown",
            "backend": intent.get("backend"),
            "model": intent.get("model"),
            "operation_id": None,
            "original_skill_hash": request.original_skill_hash,
            "patched_skill_hash": None,
            "evidence_hash": evidence["evidence_hash"],
            "timeout_seconds": intent.get("timeout_seconds"),
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "turns": 0,
            "cost_provider_credits": 0.0,
            "wall_seconds": 0.0,
        }
        _write_or_verify(output / "patch.json", result, "patch result")
        _terminal_receipt(output, result)
        shutil.rmtree(output / ".backend-work", ignore_errors=True)
        return result

    if not callable(backend):
        raise TypeError("patch backend must be callable")
    backend_name = str(getattr(backend, "backend_name", "unknown"))
    model = str(getattr(backend, "model", "unknown"))
    timeout = int(getattr(backend, "timeout_seconds", 300))
    intent = {
        "schema": "skillrace-patch-only-intent/1",
        "repair_id": request.repair_id,
        "request": request.identity(),
        "evidence_hash": evidence["evidence_hash"],
        "backend": backend_name,
        "model": model,
        "timeout_seconds": timeout,
    }
    _write_or_verify(intent_path, intent, "patch intent")
    work = output / ".backend-work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    started = time.monotonic()
    raw: Mapping[str, Any]
    try:
        try:
            value = backend(request, evidence, work)
            raw = value if isinstance(value, Mapping) else {
                "status": "error",
                "error": "backend returned no result",
            }
        except Exception as error:
            raw = {"status": "error", "error": type(error).__name__}

        status = str(raw.get("status") or "error")
        if status not in {"completed", "timeout", "error"}:
            status = "error"
        patched_hash: str | None = None
        invalid_reason = ""
        if status == "completed":
            try:
                raw_skill = pathlib.Path(str(raw.get("skill_dir") or "")).resolve()
                if work.resolve() not in raw_skill.parents:
                    raise ValueError("backend skill escapes ephemeral work directory")
                patched = validate_skill_package(raw_skill)
                _validate_only_skill_changed(request.original_skill_dir.resolve(), patched)
                destination = output / "skill"
                if destination.exists():
                    raise ValueError("patched skill destination already exists")
                shutil.copytree(patched, destination)
                validate_skill_package(destination)
                patched_hash = package_hash(destination)
                atomic_write_text(output / "skill.diff", _diff(request.original_skill_dir, destination))
            except Exception as error:
                status = "invalid_patch"
                invalid_reason = type(error).__name__
                shutil.rmtree(output / "skill", ignore_errors=True)
                (output / "skill.diff").unlink(missing_ok=True)
        result = {
            "schema": "skillrace-patch-only-result/1",
            "repair_id": request.repair_id,
            "method": request.method,
            "status": status,
            "backend": backend_name,
            "model": model,
            "operation_id": raw.get("operation_id"),
            "original_skill_hash": request.original_skill_hash,
            "patched_skill_hash": patched_hash,
            "evidence_hash": evidence["evidence_hash"],
            "timeout_seconds": timeout,
            "input_tokens": int(raw.get("input_tokens", 0) or 0),
            "output_tokens": int(raw.get("output_tokens", 0) or 0),
            "cache_read_tokens": int(raw.get("cache_read_tokens", 0) or 0),
            "turns": int(raw.get("turns", 0) or 0),
            "cost_provider_credits": float(raw.get("cost_provider_credits", 0.0) or 0.0),
            "wall_seconds": round(float(raw.get("wall_seconds", time.monotonic() - started) or 0.0), 6),
        }
        if invalid_reason:
            result["error_type"] = invalid_reason
        else:
            for key in ("error_type", "error_message"):
                if isinstance(raw.get(key), str) and raw[key]:
                    result[key] = raw[key]
        for key in (
            "pi_tool_call_count",
            "pi_mutation_count",
            "pi_required_reads_remaining",
            "pi_blocked_call_count",
            "pi_last_event_type",
        ):
            if key in raw:
                result[key] = raw[key]
        _write_or_verify(output / "patch.json", result, "patch result")
        _terminal_receipt(output, result)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def patch_campaign_failures(
    campaign: Mapping[str, Any],
    *,
    skill_name: str,
    original_skill_dir: str | pathlib.Path,
    campaign_root: str | pathlib.Path,
    output_root: str | pathlib.Path,
    backend,
    evidence_max_bytes: int,
) -> dict[str, Any]:
    """Patch each definite public failure once and publish a patch-only ledger."""

    output = pathlib.Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    ledger_path = output / "patches.json"
    if ledger_path.exists():
        return _read_object(ledger_path, "patch ledger")
    requests = select_failure_repairs(
        campaign,
        skill_name=skill_name,
        original_skill_dir=original_skill_dir,
        campaign_root=campaign_root,
        output_root=output,
        phase="public",
    )
    entries = []
    total_cost = 0.0
    for request in requests:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        evidence = build_repair_evidence(campaign, request, max_bytes=evidence_max_bytes)
        atomic_write_json(request.output_dir / "evidence.json", evidence)
        result = patch_failed_execution(request, evidence, backend=backend)
        total_cost += float(result["cost_provider_credits"])
        entries.append(
            {
                "repair_id": request.repair_id,
                "execution_id": request.execution_id,
                "attempt_id": request.attempt_id,
                "status": result["status"],
                "backend": result["backend"],
                "evidence_file_hash": file_hash(request.output_dir / "evidence.json"),
                "patch_file_hash": file_hash(request.output_dir / "patch.json"),
                "receipt_file_hash": file_hash(request.output_dir / "receipt.json"),
            }
        )
    ledger = {
        "schema": "skillrace-patch-only-ledger/1",
        "method": campaign.get("method"),
        "skill_name": skill_name,
        "source_campaign_hash": canonical_json_hash(campaign),
        "original_skill_hash": package_hash(original_skill_dir),
        "failed_public_executions": len(requests),
        "patch_executions": len(requests),
        "patch_executions_counted_in_search_budget": False,
        "evidence_max_bytes": evidence_max_bytes,
        "patches": entries,
        "cost_provider_credits": round(total_cost, 12),
    }
    atomic_write_json(ledger_path, ledger)
    return ledger
