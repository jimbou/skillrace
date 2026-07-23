"""Guided Pi backend for one read-then-patch ``SKILL.md`` repair."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import time
import uuid
from collections.abc import Mapping
from typing import Any

from .io_utils import atomic_write_json, canonical_json_hash, file_hash
from .model_policy import (
    AGENT_MODELS,
    has_known_provider_credit_rate,
    provider_credits_for_known_model,
)
from .repair_validation import FailureRepairRequest
from .revise_skill import copy_base_skill_package, package_hash, validate_skill_package


PI_PATCH_SYSTEM_PROMPT = (
    "You are making one blind repair to the coding-agent skill in /workspace. "
    "Before choosing the edit, read the complete /workspace/SKILL.md and the complete "
    "/evidence/repair-context.json. Consider every evidence section, especially exact "
    "checker failures and, when present, ordered reasoning episodes, thinking, tool "
    "calls, and tool results. Then edit or write only /workspace/SKILL.md. "
    "Prefer the smallest additive clarification or guardrail that addresses the "
    "failure. Preserve useful existing guidance and do not rewrite unrelated sections; "
    "remove or replace text only when it directly caused the failure. Add actionable "
    "procedural guidance that could change a future agent's behavior. A cosmetic-only "
    "edit, docstring rewording, or unsupported claim that current behavior already "
    "handles the failure is not a repair. Make the strongest "
    "general procedural correction supported "
    "by the evidence, without memorizing concrete test values. You must not rerun or "
    "execute the failure, run any test, invoke a checker, replay an agent, validate the "
    "patch, repair the failed artifact, install packages, access the network directly, "
    "or iterate patch-and-test. Do not edit any other file. Do not provide a repair "
    "rationale. After one completed SKILL.md edit, stop without reading it again."
)

GUIDED_PATCH_RUNNER = (
    pathlib.Path(__file__).resolve().parents[1]
    / "images"
    / "pi-base"
    / "guided_patch.mjs"
)


def _usage(path: pathlib.Path) -> tuple[int, int, int, int]:
    incoming = outgoing = cached = turns = 0
    paths = [path] if path.is_file() else sorted(path.glob("*.jsonl"))
    for candidate in paths:
        if candidate.name == "guided-events.jsonl":
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            try:
                message = json.loads(line).get("message", {})
            except json.JSONDecodeError:
                continue
            if message.get("role") != "assistant":
                continue
            usage = message.get("usage") or {}
            incoming += int(usage.get("input", 0) or 0)
            outgoing += int(usage.get("output", 0) or 0)
            cached += int(usage.get("cacheRead", 0) or 0)
            turns += 1
    return incoming, outgoing, cached, turns


def _guided_diagnostics(accounting: pathlib.Path) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    summary = accounting / "guided-summary.json"
    if summary.is_file():
        try:
            value = json.loads(summary.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            value = {}
        if isinstance(value, Mapping):
            for key in (
                "turn_count",
                "tool_call_count",
                "mutation_count",
                "required_reads_remaining",
                "blocked_call_count",
            ):
                if isinstance(value.get(key), int):
                    diagnostics[key] = value[key]
            if isinstance(value.get("error"), str):
                diagnostics["error"] = value["error"][-500:]
    events = accounting / "guided-events.jsonl"
    if events.is_file():
        event_tool_calls = 0
        for line in events.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, Mapping) or not isinstance(event.get("type"), str):
                continue
            diagnostics["last_event_type"] = event["type"]
            if event["type"] == "turn_end" and isinstance(event.get("turn"), int):
                diagnostics["turn_count"] = max(
                    int(diagnostics.get("turn_count", 0)), event["turn"]
                )
            if event["type"] == "tool_call":
                event_tool_calls += 1
        diagnostics["tool_call_count"] = max(
            int(diagnostics.get("tool_call_count", 0)), event_tool_calls
        )
    return diagnostics


def make_pi_patcher(
    *,
    model: str,
    timeout_seconds: int = 300,
    image: str | None = None,
    run_fn=subprocess.run,
    cleanup_fn=subprocess.run,
):
    """Return a Pi backend with guided reads but no execution or replay tool."""

    if model not in AGENT_MODELS:
        raise ValueError("Pi patch model must support structured agent tools")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 600:
        raise ValueError("Pi patch timeout must be in 1..600")
    image = image or f"skillrace/pi-base:0.73.1-{model}"
    if not isinstance(image, str) or not image:
        raise ValueError("Pi patch image must be nonempty")
    if not callable(run_fn) or not callable(cleanup_fn):
        raise TypeError("Pi process functions must be callable")
    if not GUIDED_PATCH_RUNNER.is_file():
        raise ValueError("guided Pi patch runner is missing")
    config: dict[str, Any] = {
        "backend": "pi",
        "model": model,
        "timeout_seconds": timeout_seconds,
        "image": image,
        "tools": "read,grep,edit,write",
        "thinking_level": "medium",
        "max_turns": 10,
        "prompt_version": "skillrace-pi-patch/8",
        "runner_sha256": file_hash(GUIDED_PATCH_RUNNER),
    }

    def patcher(
        request: FailureRepairRequest,
        evidence: Mapping[str, Any],
        work_dir: pathlib.Path,
    ) -> dict[str, Any]:
        source = validate_skill_package(request.original_skill_dir)
        if package_hash(source) != request.original_skill_hash:
            raise ValueError("Pi patch source differs from request")
        payload = evidence.get("reviser_payload")
        if not isinstance(payload, Mapping) or evidence.get("evidence_hash") != canonical_json_hash(payload):
            raise ValueError("Pi patch evidence identity mismatch")
        work = pathlib.Path(work_dir).resolve()
        work.mkdir(parents=True, exist_ok=True)
        skill = copy_base_skill_package(source, work / "skill")
        evidence_dir = work / "evidence"
        evidence_dir.mkdir()
        context = {
            "schema": payload.get("schema"),
            "common": {
                "original_skill_hash": payload.get("original_skill_hash"),
                "failure_core": payload.get("failure_core"),
            },
            "method_evidence": (
                payload.get("method_evidence")
                if request.method == "skillrace"
                else None
            ),
        }
        context_path = evidence_dir / "repair-context.json"
        atomic_write_json(context_path, context)
        prompt_path = evidence_dir / "repair-prompt.txt"
        prompt_path.write_text(
            "Read both /workspace/SKILL.md and /evidence/repair-context.json in full "
            "before editing. Read each exactly once and do not grep before both direct "
            "reads complete. Use the evidence to make one strong, general repair to "
            "/workspace/SKILL.md. Do not execute, test, verify, or replay anything. "
            "After the single edit, stop.\n",
            encoding="utf-8",
        )
        system_prompt_path = evidence_dir / "system-prompt.txt"
        system_prompt_path.write_text(PI_PATCH_SYSTEM_PROMPT + "\n", encoding="utf-8")
        accounting = work / "accounting"
        accounting.mkdir()
        empty_home = work / "pi-home"
        empty_home.mkdir()
        model_catalog = (
            pathlib.Path(__file__).resolve().parents[1]
            / "images"
            / "pi-base"
            / f"models.yunwu.{model}.json"
        )
        if not model_catalog.is_file():
            raise ValueError(f"Pi patch model catalog is missing: {model}")
        shutil.copy2(model_catalog, empty_home / "models.json")
        container = "skillrace-patch-" + uuid.uuid4().hex[:20]
        mounts = [
            "-v", f"{skill}:/workspace:rw",
            "-v", f"{context_path}:/evidence/repair-context.json:ro",
            "-v", f"{prompt_path}:/evidence/repair-prompt.txt:ro",
            "-v", f"{system_prompt_path}:/evidence/system-prompt.txt:ro",
            "-v", f"{GUIDED_PATCH_RUNNER}:/runtime/guided_patch.mjs:ro",
            "-v", f"{accounting}:/accounting:rw",
            "-v", f"{empty_home}:/pi-home:rw",
        ]
        argv = [
            "docker", "run", "--rm", "--network=host", "--name", container,
            "-e", "yunwu_key", "-e", "PI_CODING_AGENT_DIR=/pi-home",
            "-e", f"PI_MODEL={model}", "-e", "PI_PROVIDER=yunwu",
            "-e", "PI_ALLOWED_TOOLS=read,grep,edit,write",
            "-e", "PI_THINKING_LEVEL=medium", "-e", "PI_MAX_TURNS=10",
            "-e", "PI_REPAIR_SKILL_PATH=/workspace/SKILL.md",
            "-e", "PI_REPAIR_CONTEXT_PATH=/evidence/repair-context.json",
            "-e", "PI_REPAIR_PROMPT_PATH=/evidence/repair-prompt.txt",
            "-e", "PI_SYSTEM_PROMPT_PATH=/evidence/system-prompt.txt",
            "-e", "PI_ACCOUNTING_DIR=/accounting",
            *mounts,
            "-w", "/workspace", image,
            "node", "/runtime/guided_patch.mjs",
        ]
        started = time.monotonic()
        status = "error"
        error_type = ""
        error_message = ""
        usage_before_cleanup = (0, 0, 0, 0)
        diagnostics_before_cleanup: dict[str, Any] = {}
        operation_id = "repair.pi." + canonical_json_hash(
            {
                "request": request.identity(),
                "evidence_hash": evidence["evidence_hash"],
                "config": config,
            }
        )
        try:
            try:
                completed = run_fn(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
                status = "completed" if completed.returncode == 0 else "error"
                if completed.returncode != 0:
                    error_type = f"container_exit_{completed.returncode}"
                    error_message = str(completed.stderr or completed.stdout or "")[-500:]
            except subprocess.TimeoutExpired:
                status = "timeout"
                error_type = "timeout"
        finally:
            # A timed-out Docker client can leave the Pi container alive until the
            # forced removal below. Snapshot already-flushed usage first: some
            # runtimes remove or replace the session during forced teardown.
            usage_before_cleanup = _usage(accounting)
            diagnostics_before_cleanup = _guided_diagnostics(accounting)
            cleanup_fn(
                ["docker", "rm", "-f", container],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        usage_after_cleanup = _usage(accounting)
        incoming, outgoing, cached, turns = tuple(
            max(before, after)
            for before, after in zip(
                usage_before_cleanup, usage_after_cleanup, strict=True
            )
        )
        diagnostics_after_cleanup = _guided_diagnostics(accounting)
        diagnostics = dict(diagnostics_after_cleanup)
        for key, value in diagnostics_before_cleanup.items():
            if isinstance(value, int):
                diagnostics[key] = max(int(diagnostics.get(key, 0)), value)
            elif key not in diagnostics:
                diagnostics[key] = value
        cost = (
            provider_credits_for_known_model(
                # Pi session usage reports uncached input and cache reads as
                # disjoint counters. The shared rate helper accepts total input
                # with its cached subset identified separately.
                model, incoming + cached, outgoing, cached_input_tokens=cached
            )
            if has_known_provider_credit_rate(model)
            else 0.0
        )
        shutil.rmtree(accounting, ignore_errors=True)
        shutil.rmtree(empty_home, ignore_errors=True)
        for staged in evidence_dir.iterdir():
            staged.unlink()
        evidence_dir.rmdir()
        try:
            from .patch_only import _validate_only_skill_changed

            validate_skill_package(skill)
            _validate_only_skill_changed(source, skill)
            # The patch artifact is the semantic terminal output. If the provider
            # fails only after the one edit while Pi is stopping, do not discard a
            # complete structurally valid patch or issue another model attempt.
            status = "completed"
        except Exception:
            if status == "completed":
                status = "error"
                error_type = "invalid_patch"
        result = {
            "status": status,
            "backend": "pi",
            "model": model,
            "operation_id": operation_id,
            "input_tokens": incoming,
            "output_tokens": outgoing,
            "cache_read_tokens": cached,
            "turns": turns,
            "cost_provider_credits": cost,
            "wall_seconds": round(time.monotonic() - started, 6),
            "timeout_seconds": timeout_seconds,
        }
        if isinstance(diagnostics.get("tool_call_count"), int):
            result["pi_tool_call_count"] = diagnostics["tool_call_count"]
        if isinstance(diagnostics.get("mutation_count"), int):
            result["pi_mutation_count"] = diagnostics["mutation_count"]
        if isinstance(diagnostics.get("required_reads_remaining"), int):
            result["pi_required_reads_remaining"] = diagnostics[
                "required_reads_remaining"
            ]
        if isinstance(diagnostics.get("blocked_call_count"), int):
            result["pi_blocked_call_count"] = diagnostics["blocked_call_count"]
        if isinstance(diagnostics.get("last_event_type"), str):
            result["pi_last_event_type"] = diagnostics["last_event_type"]
        if status == "completed":
            result["skill_dir"] = str(skill)
        elif error_type:
            result["error_type"] = error_type
            result["error_message"] = error_message or str(
                diagnostics.get("error", "")
            )
        return result

    patcher.backend_name = "pi"
    patcher.model = model
    patcher.timeout_seconds = timeout_seconds
    patcher.config = config
    return patcher
