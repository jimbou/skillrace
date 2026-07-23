"""Per-failure, post-search skill repair contracts.

Selection is deliberately per counted failed execution, not per deduplicated defect
signature.  This module contains no paid/external action: it freezes replay requests and
method-assisted evidence before the exactly-once executor consumes them.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
import pathlib
import re
import hashlib
from collections.abc import Mapping
from typing import Any

from .closeai import (
    chat,
    chat_request_identity,
    is_nonproduction_chat_fixture,
    validate_chat_result,
)
from .io_utils import (
    atomic_write_json,
    atomic_write_text,
    canonical_json_bytes,
    canonical_json_hash,
    file_hash,
    resolve_campaign_path,
)
from .revise_skill import (
    copy_base_skill_package,
    normalize_revised_skill,
    package_hash,
    validate_skill_package,
)
from .rq3_confirmation import failure_signature


REPAIR_SCHEMA = "skillrace-failure-repair-request/1"
REPAIR_EVIDENCE_SCHEMA = "skillrace-failure-repair-evidence/1"
REPAIR_STATUSES = (
    "repaired",
    "same_failure",
    "different_failure",
    "timeout",
    "error",
    "inconclusive",
)
REPAIR_METHODS = ("random", "greybox", "skillrace")
RQ1_REPAIR_EVIDENCE_MAX_BYTES = 32_000
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class UncertainRepairOutcomeError(RuntimeError):
    """A paid repair action may have occurred but lacks terminal evidence."""


REPAIR_PATCH_SYSTEM_PROMPT = (
    "You repair a coding-agent SKILL.md from one failed execution. Use every piece "
    "of diagnostic evidence present, including reasoning, branch, guard, and mutation "
    "evidence when supplied. Correct the general procedural guidance that allowed the "
    "failure. Generalize beyond this exact test: do not copy its concrete filenames, "
    "literal values, expected output, or checker details into the skill. Preserve the "
    "skill's purpose and useful existing guidance. Output only the complete revised "
    "SKILL.md."
)


@dataclasses.dataclass(frozen=True)
class FailureRepairRequest:
    method: str
    skill_name: str
    execution_id: str
    attempt_id: str
    candidate_id: str
    case_dir: pathlib.Path
    original_skill_dir: pathlib.Path
    original_skill_hash: str
    failed_property_ids: tuple[str, ...]
    failure_signatures: tuple[str, ...]
    run_dir: pathlib.Path
    output_dir: pathlib.Path
    repair_id: str

    def identity(self) -> dict[str, Any]:
        """Return the path-independent durable identity for this repair action."""

        return {
            "schema": REPAIR_SCHEMA,
            "method": self.method,
            "skill_name": self.skill_name,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "candidate_id": self.candidate_id,
            "original_skill_hash": self.original_skill_hash,
            "failed_property_ids": list(self.failed_property_ids),
            "failure_signatures": list(self.failure_signatures),
        }


def _safe_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"failed execution has invalid {label}")
    return value


def _within(root: pathlib.Path, raw: Any, label: str) -> pathlib.Path:
    try:
        return resolve_campaign_path(root, raw, label)
    except ValueError as error:
        raise ValueError(str(error).replace("campaign", "failed execution", 1)) from error


def _attempt_verdicts(
    attempt: Mapping[str, Any], run_dir: pathlib.Path | None = None
) -> list[dict[str, Any]]:
    result = attempt.get("result")
    result = result if isinstance(result, Mapping) else {}
    raw = result.get("verdicts")
    if isinstance(raw, list):
        return [dict(row) for row in raw if isinstance(row, Mapping)]
    if run_dir is None:
        return []
    path = run_dir / "verdicts.json"
    if path.is_symlink():
        raise ValueError("failed execution verdict receipt may not be a symlink")
    try:
        linked = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("failed execution lacks a readable verdict receipt") from error
    if not isinstance(linked, list) or any(
        not isinstance(row, Mapping) for row in linked
    ):
        raise ValueError("failed execution verdict receipt must be a list of objects")
    return [dict(row) for row in linked]


def _definite_failures(
    attempt: Mapping[str, Any], run_dir: pathlib.Path | None = None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if attempt.get("consume_budget") is not True:
        return (), ()
    violated = attempt.get("violated")
    if not isinstance(violated, list) or not violated:
        return (), ()
    result = attempt.get("result")
    result = result if isinstance(result, Mapping) else {}
    verdicts = _attempt_verdicts(attempt, run_dir)
    definite = {
        verdict.get("property_id")
        for verdict in verdicts
        if isinstance(verdict, Mapping)
        and verdict.get("holds") is False
        and verdict.get("violated") is True
        and isinstance(verdict.get("property_id"), str)
    }
    property_ids: list[str] = []
    for raw in violated:
        if isinstance(raw, str) and raw and raw in definite and raw not in property_ids:
            property_ids.append(raw)
    if not property_ids:
        return (), ()
    signatures = result.get("failure_signatures")
    signatures = signatures if isinstance(signatures, Mapping) else {}
    values: list[str] = []
    for property_id in property_ids:
        signature = signatures.get(property_id)
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            verdict = next(
                row for row in verdicts if row.get("property_id") == property_id
            )
            signature = failure_signature(verdict)
        values.append(signature)
    return tuple(property_ids), tuple(values)


def select_failure_repairs(
    campaign: Mapping[str, Any],
    *,
    skill_name: str,
    original_skill_dir: str | pathlib.Path,
    campaign_root: str | pathlib.Path,
    output_root: str | pathlib.Path,
    phase: str,
) -> list[FailureRepairRequest]:
    """Freeze one request for every raw definite failed public execution."""

    if phase != "public":
        raise ValueError("hidden tests must never be selected for repair")
    if not isinstance(campaign, Mapping) or campaign.get("complete") is not True:
        raise ValueError("repair selection requires a complete public campaign")
    method = campaign.get("method")
    if method not in REPAIR_METHODS:
        raise ValueError("campaign has unsupported repair method")
    skill_name = _safe_identifier(skill_name, "skill name")
    skill = validate_skill_package(original_skill_dir)
    skill_hash = package_hash(skill)
    root = pathlib.Path(campaign_root).resolve()
    repairs = pathlib.Path(output_root).resolve()
    attempts = campaign.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError("campaign attempts are missing")
    requests: list[FailureRepairRequest] = []
    seen_attempts: set[str] = set()
    for raw in attempts:
        if not isinstance(raw, Mapping):
            continue
        if raw.get("consume_budget") is not True or not raw.get("violated"):
            continue
        run_dir = _within(root, raw.get("run") or raw.get("run_dir"), "run path")
        property_ids, signatures = _definite_failures(raw, run_dir)
        if not property_ids:
            continue
        execution_id = _safe_identifier(raw.get("execution_id"), "execution ID")
        attempt_id = _safe_identifier(raw.get("attempt_id"), "attempt ID")
        candidate_id = _safe_identifier(raw.get("candidate_id"), "candidate ID")
        if attempt_id in seen_attempts:
            raise ValueError("campaign contains duplicate failed attempt identity")
        seen_attempts.add(attempt_id)
        case_dir = _within(root, raw.get("case") or raw.get("case_dir"), "case path")
        identity = {
            "schema": REPAIR_SCHEMA,
            "method": method,
            "skill_name": skill_name,
            "execution_id": execution_id,
            "attempt_id": attempt_id,
            "candidate_id": candidate_id,
            "original_skill_hash": skill_hash,
            "failed_property_ids": list(property_ids),
            "failure_signatures": list(signatures),
        }
        repair_id = canonical_json_hash(identity)[:24]
        requests.append(
            FailureRepairRequest(
                method=method,
                skill_name=skill_name,
                execution_id=execution_id,
                attempt_id=attempt_id,
                candidate_id=candidate_id,
                case_dir=case_dir,
                original_skill_dir=skill,
                original_skill_hash=skill_hash,
                failed_property_ids=property_ids,
                failure_signatures=signatures,
                run_dir=run_dir,
                output_dir=repairs / repair_id,
                repair_id=repair_id,
            )
        )
    return sorted(requests, key=lambda request: (request.execution_id, request.attempt_id))


def _clip(value: Any, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[:limit]


def _evidence_value(value: Any) -> Any:
    """Return a deterministic JSON-safe copy without model-produced summarization."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _evidence_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_evidence_value(item) for item in value]
    return str(value)


def _bounded_trace_value(value: Any, *, max_bytes: int) -> Any:
    """Keep small structured trace values exact and hash/excerpt larger ones."""

    safe = _evidence_value(value)
    raw = canonical_json_bytes(safe)
    if len(raw) <= max_bytes:
        return safe
    marker = b"... [truncated] ..."
    budget = max(0, max_bytes - len(marker))
    head = budget // 2
    tail = budget - head
    excerpt = raw[:head] + marker + raw[-tail:]
    return {
        "bytes": len(raw),
        "excerpt": excerpt.decode("utf-8", errors="replace"),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "truncated": True,
    }


def _bounded_trace_text(value: Any, *, max_chars: int) -> str:
    """Preserve both ends of a trace string without unbounded prompt growth."""

    text = str(value or "")
    if len(text) <= max_chars:
        return text
    marker = "\n... [truncated] ...\n"
    budget = max(0, max_chars - len(marker))
    head = budget // 2
    tail = budget - head
    return text[:head] + marker + text[-tail:]


def _text_evidence(path: pathlib.Path, *, max_bytes: int) -> dict[str, Any] | None:
    """Read one contained evidence file with an exact hash and bounded excerpt."""

    if path.is_symlink() or not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    truncated = len(raw) > max_bytes
    if truncated:
        marker = b"\n... [middle truncated; hash covers complete file] ...\n"
        payload_budget = max(0, max_bytes - len(marker))
        head = payload_budget // 2
        tail = payload_budget - head
        excerpt = raw[:head] + marker + raw[-tail:]
    else:
        excerpt = raw
    return {
        "bytes": len(raw),
        "content": excerpt.decode("utf-8", errors="replace"),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "truncated": truncated,
    }


def _saved_input_files(request: FailureRepairRequest) -> list[dict[str, str]]:
    candidate_path = request.case_dir / "candidate.json"
    if candidate_path.is_symlink() or not candidate_path.is_file():
        return []
    try:
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    sanity = candidate.get("sanity") if isinstance(candidate, Mapping) else None
    paths = sanity.get("required_paths") if isinstance(sanity, Mapping) else None
    if not isinstance(paths, list):
        return []
    return [
        {"path": path}
        for path in paths
        if isinstance(path, str) and path
    ]


def _saved_failed_artifact(request: FailureRepairRequest) -> dict[str, Any]:
    for relative in ("logs/workspace.diff", "workspace.diff"):
        evidence = _text_evidence(request.run_dir / relative, max_bytes=3500)
        if evidence is not None:
            return {"workspace_diff": evidence}
    return {}


def _saved_executable_conditions(
    request: FailureRepairRequest, verdicts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    wanted = set(request.failed_property_ids)
    case = request.case_dir.resolve()
    for verdict in verdicts:
        property_id = verdict.get("property_id")
        raw_path = verdict.get("script")
        if property_id not in wanted or not isinstance(raw_path, str) or not raw_path:
            continue
        try:
            script = resolve_campaign_path(case, raw_path, "checker script")
        except ValueError:
            continue
        if script != case and case not in script.parents:
            continue
        evidence = _text_evidence(script, max_bytes=2000)
        if evidence is not None:
            conditions.append(
                {"property_id": property_id, "checker_script": evidence}
            )
    return conditions


def _attempt(campaign: Mapping[str, Any], request: FailureRepairRequest) -> Mapping[str, Any]:
    attempts = campaign.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError("campaign attempts are missing")
    matches = [
        attempt
        for attempt in attempts
        if isinstance(attempt, Mapping)
        and attempt.get("attempt_id") == request.attempt_id
    ]
    if len(matches) != 1:
        raise ValueError("repair request does not identify one campaign attempt")
    return matches[0]


def _trace_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, Mapping)
    )


def _saved_reasoning_episodes(run_dir: pathlib.Path) -> list[dict[str, Any]] | None:
    """Join SkillRACE's saved episode spans to the underlying Pi tool trace."""

    episodes_path = run_dir / "episodes.json"
    session_path = run_dir / "raw" / "session.jsonl"
    if (
        not episodes_path.is_file()
        or episodes_path.is_symlink()
        or not session_path.is_file()
        or session_path.is_symlink()
    ):
        return None
    try:
        episode_document = json.loads(episodes_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    raw_episodes = (
        episode_document.get("episodes")
        if isinstance(episode_document, Mapping)
        else None
    )
    if not isinstance(raw_episodes, list) or not raw_episodes:
        return None

    rows: list[Mapping[str, Any]] = []
    try:
        for line in session_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, Mapping):
                rows.append(row)
    except (OSError, UnicodeDecodeError):
        return None
    results: dict[str, dict[str, Any]] = {}
    for row in rows:
        message = row.get("message")
        if not isinstance(message, Mapping) or message.get("role") != "toolResult":
            continue
        call_id = message.get("toolCallId")
        if isinstance(call_id, str) and call_id:
            results[call_id] = {
                "name": str(message.get("toolName") or ""),
                "content": _trace_text(message.get("content")),
                "is_error": message.get("isError") is True,
            }

    calls: list[dict[str, Any]] = []
    for row in rows:
        message = row.get("message")
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        reasoning = " ".join(
            str(
                block.get("thinking")
                or block.get("reasoning")
                or block.get("reasoning_summary")
                or ""
            ).strip()
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "thinking"
        ).strip()
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") != "toolCall":
                continue
            call_id = block.get("id")
            result = results.get(call_id, {}) if isinstance(call_id, str) else {}
            index = len(calls) + 1
            calls.append(
                {
                    "call_index": index,
                    "name": str(block.get("name") or ""),
                    "arguments": _bounded_trace_value(
                        block.get("arguments") or {}, max_bytes=240
                    ),
                    "reasoning": reasoning,
                    "result": {
                        "call_index": index,
                        "name": str(result.get("name") or block.get("name") or ""),
                        "content": _bounded_trace_text(
                            result.get("content"), max_chars=320
                        ),
                        "is_error": result.get("is_error") is True,
                    },
                }
            )
    if not calls:
        return None

    assembled: list[dict[str, Any]] = []
    previous_end = 0
    for raw in raw_episodes:
        if not isinstance(raw, Mapping):
            return None
        start = raw.get("start_call")
        end = raw.get("end_call")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start != previous_end + 1
            or end < start
            or end > len(calls)
        ):
            return None
        selected = calls[start - 1 : end]
        assembled.append(
            {
                "intent": _bounded_trace_text(raw.get("intent"), max_chars=400),
                "reasoning": _bounded_trace_text(
                    raw.get("opening_reasoning") or selected[0]["reasoning"],
                    max_chars=1200,
                ),
                "tool_span": {"start_call": start, "end_call": end},
                "tool_calls": [
                    {
                        "call_index": call["call_index"],
                        "name": call["name"],
                        "arguments": call["arguments"],
                    }
                    for call in selected
                ],
                "tool_results": [call["result"] for call in selected],
                "what_it_did": _bounded_trace_text(
                    raw.get("what_it_did"), max_chars=600
                ),
                "outcome": _bounded_trace_text(raw.get("outcome"), max_chars=800),
            }
        )
        previous_end = end
    if previous_end != len(calls):
        return None
    return assembled


def _bounded_payload(payload: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1024:
        raise ValueError("repair evidence max_bytes must be an integer of at least 1024")
    value = copy.deepcopy(payload)
    evidence = value["method_evidence"]
    # Remove the least essential tail evidence first while preserving the shared core.
    while len(canonical_json_bytes(value)) > max_bytes and evidence["reasoning_episodes"]:
        # Episodes are chronological; retain the failure-adjacent tail first.
        evidence["reasoning_episodes"].pop(0)
    while len(canonical_json_bytes(value)) > max_bytes and evidence["tree_path"]:
        evidence["tree_path"].pop()
    if len(canonical_json_bytes(value)) > max_bytes:
        evidence["branch_evidence"] = {}
    if len(canonical_json_bytes(value)) > max_bytes:
        evidence["guard_mutation"] = {}
    if len(canonical_json_bytes(value)) > max_bytes:
        raise ValueError("shared failure core exceeds repair evidence byte budget")
    return value


def build_repair_evidence(
    campaign: Mapping[str, Any],
    request: FailureRepairRequest,
    *,
    max_bytes: int,
) -> dict[str, Any]:
    """Build bounded method-assisted evidence without exposing producer identity."""

    if campaign.get("method") != request.method:
        raise ValueError("repair campaign method differs from request")
    attempt = _attempt(campaign, request)
    provenance = attempt.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    result = attempt.get("result")
    result = result if isinstance(result, Mapping) else {}
    verdicts = _attempt_verdicts(attempt, request.run_dir)
    failures = []
    for property_id, signature in zip(
        request.failed_property_ids, request.failure_signatures, strict=True
    ):
        verdict = next(
            (
                row
                for row in verdicts
                if isinstance(row, Mapping) and row.get("property_id") == property_id
            ),
            {},
        )
        failures.append(
            {
                "property_id": property_id,
                "failure_signature": signature,
                "checker_error": str(
                    verdict.get("detail") or verdict.get("failure_summary") or ""
                ),
                # Historical consumers use this name. It intentionally carries the
                # same exact checker text rather than a second summary.
                "mechanical_error": str(
                    verdict.get("detail") or verdict.get("failure_summary") or ""
                ),
            }
        )
    explicit_inputs = provenance.get("input_files")
    explicit_artifact = result.get("failed_artifact") or attempt.get("failed_artifact")
    explicit_conditions = (
        result.get("executable_conditions")
        or attempt.get("executable_conditions")
    )
    failure_core = {
        "candidate_id": request.candidate_id,
        "task": str(provenance.get("task_nl") or ""),
        "environment": str(provenance.get("env_nl") or ""),
        "input_files": _evidence_value(
            explicit_inputs
            if explicit_inputs
            else _saved_input_files(request)
        ),
        "failed_artifact": _evidence_value(
            explicit_artifact
            if explicit_artifact
            else _saved_failed_artifact(request)
        ),
        "executable_conditions": _evidence_value(
            explicit_conditions
            if explicit_conditions
            else _saved_executable_conditions(request, verdicts)
        ),
        "failures": failures,
        "artifact_diff_summary": str(
            result.get("workspace_diff_summary")
            or attempt.get("workspace_diff_summary")
            or ""
        ),
    }
    method_evidence: dict[str, Any] = {
        "reasoning_episodes": [],
        "tree_path": [],
        "guard_mutation": {},
        "branch_evidence": {},
    }
    if request.method == "skillrace":
        episodes = _saved_reasoning_episodes(request.run_dir)
        if episodes is None:
            episodes = provenance.get("reasoning_episodes")
        if isinstance(episodes, list):
            method_evidence["reasoning_episodes"] = [
                {
                    "intent": str(row.get("intent") or ""),
                    "reasoning": str(
                        row.get("reasoning")
                        or row.get("thinking")
                        or row.get("reasoning_summary")
                        or ""
                    ),
                    "tool_calls": _evidence_value(row.get("tool_calls") or []),
                    "tool_results": _evidence_value(row.get("tool_results") or []),
                    "tool_span": _evidence_value(row.get("tool_span") or {}),
                    "what_it_did": str(row.get("what_it_did") or ""),
                    "outcome": str(row.get("outcome") or ""),
                }
                for row in episodes
                if isinstance(row, Mapping)
            ]
        tree_path = provenance.get("tree_path")
        if isinstance(tree_path, list):
            method_evidence["tree_path"] = [_clip(item, 160) for item in tree_path[:16]]
        method_evidence["guard_mutation"] = {
            "guard": _clip(provenance.get("guard")),
            "mutation": _clip(provenance.get("mutation")),
            "targeted_property": _clip(provenance.get("targeted_property"), 128),
        }
        classification = attempt.get("classification")
        classification = classification if isinstance(classification, Mapping) else {}
        method_evidence["branch_evidence"] = {
            "intended_branch": str(provenance.get("intended_branch") or ""),
            "observed_branch": str(provenance.get("observed_branch") or ""),
            "branch_outcome": _clip(
                classification.get("branch_outcome") or classification.get("outcome"),
                128,
            ),
            "targeting": _clip(classification.get("targeting"), 128),
        }
    payload = _bounded_payload(
        {
            "schema": REPAIR_EVIDENCE_SCHEMA,
            "original_skill_hash": request.original_skill_hash,
            "failure_core": failure_core,
            "method_evidence": method_evidence,
        },
        max_bytes,
    )
    used_bytes = len(canonical_json_bytes(payload))
    return {
        "schema": REPAIR_EVIDENCE_SCHEMA,
        "repair_id": request.repair_id,
        "failure_core": failure_core,
        "method_evidence": payload["method_evidence"],
        "reviser_payload": payload,
        "evidence_hash": canonical_json_hash(payload),
        "accounting": {
            "budget_unit": "canonical-json-utf8-bytes/1",
            "max_bytes": max_bytes,
            "used_bytes": used_bytes,
        },
    }


def _read_object(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _write_or_verify(path: pathlib.Path, value: Mapping[str, Any], label: str) -> bool:
    frozen = copy.deepcopy(dict(value))
    if path.exists():
        if _read_object(path, label) != frozen:
            raise ValueError(f"{label} identity mismatch")
        return False
    atomic_write_json(path, frozen)
    return True


def _safe_cost(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"{label} must be finite and non-negative")
    return float(value)


def _normalize_patch_result(
    raw: Any, request: FailureRepairRequest, patch_root: pathlib.Path
) -> tuple[dict[str, Any], pathlib.Path | None]:
    value = dict(raw) if isinstance(raw, Mapping) else {}
    status = value.get("status")
    if status != "completed":
        return (
            {
                "schema": "skillrace-failure-repair-patch/1",
                "repair_id": request.repair_id,
                "status": "error",
                "operation_id": str(value.get("operation_id") or ""),
                "input_tokens": int(value.get("input_tokens", 0) or 0),
                "output_tokens": int(value.get("output_tokens", 0) or 0),
                "cost_provider_credits": _safe_cost(value.get("cost_provider_credits", 0.0) or 0.0, "patch cost"),
                "error": _clip(value.get("error") or "patcher returned no completed skill", 500),
            },
            None,
        )
    raw_skill = value.get("skill_dir")
    if not isinstance(raw_skill, str) or not raw_skill:
        raise ValueError("completed repair patch lacks skill_dir")
    skill = validate_skill_package(raw_skill)
    root = patch_root.resolve()
    if skill != root and root not in skill.parents:
        raise ValueError("patched skill escapes repair patch directory")
    record = {
        "schema": "skillrace-failure-repair-patch/1",
        "repair_id": request.repair_id,
        "status": "completed",
        "operation_id": str(value.get("operation_id") or ""),
        "skill_path": skill.relative_to(request.output_dir.resolve()).as_posix(),
        "skill_hash": package_hash(skill),
        "input_tokens": int(value.get("input_tokens", 0) or 0),
        "output_tokens": int(value.get("output_tokens", 0) or 0),
        "cost_provider_credits": _safe_cost(value.get("cost_provider_credits", 0.0) or 0.0, "patch cost"),
        "error": "",
    }
    return record, skill


def _verdict_signature(verdict: Mapping[str, Any]) -> str | None:
    supplied = verdict.get("failure_signature")
    if isinstance(supplied, str) and re.fullmatch(r"[0-9a-f]{64}", supplied):
        return supplied
    try:
        return failure_signature(verdict)
    except ValueError:
        return None


def _classify_replay(
    raw: Any, request: FailureRepairRequest
) -> tuple[str, list[dict[str, Any]]]:
    value = dict(raw) if isinstance(raw, Mapping) else {}
    external_status = value.get("status")
    if external_status in {"timeout", "error", "inconclusive"}:
        return str(external_status), []
    if external_status != "completed":
        return "error", []
    raw_verdicts = value.get("verdicts")
    if not isinstance(raw_verdicts, list):
        return "inconclusive", []
    verdicts = [dict(row) for row in raw_verdicts if isinstance(row, Mapping)]
    original = dict(zip(request.failed_property_ids, request.failure_signatures, strict=True))
    for verdict in verdicts:
        property_id = verdict.get("property_id")
        if (
            verdict.get("holds") is False
            and verdict.get("violated") is True
            and property_id in original
            and _verdict_signature(verdict) == original[property_id]
        ):
            return "same_failure", verdicts
    if any(
        verdict.get("holds") is False and verdict.get("violated") is True
        for verdict in verdicts
    ):
        return "different_failure", verdicts
    by_property = {
        verdict.get("property_id"): verdict
        for verdict in verdicts
        if isinstance(verdict.get("property_id"), str)
    }
    if all(
        property_id in by_property and by_property[property_id].get("holds") is True
        for property_id in request.failed_property_ids
    ):
        return "repaired", verdicts
    return "inconclusive", verdicts


def _final_result(
    request: FailureRepairRequest,
    patch: Mapping[str, Any],
    replay: Mapping[str, Any],
) -> dict[str, Any]:
    status, verdicts = _classify_replay(replay, request)
    patch_cost = _safe_cost(patch.get("cost_provider_credits", 0.0) or 0.0, "patch cost")
    replay_cost = _safe_cost(replay.get("cost_provider_credits", 0.0) or 0.0, "replay cost")
    return {
        "schema": "skillrace-failure-repair-result/1",
        "repair_id": request.repair_id,
        "status": status,
        "method": request.method,
        "skill_name": request.skill_name,
        "execution_id": request.execution_id,
        "attempt_id": request.attempt_id,
        "candidate_id": request.candidate_id,
        "failed_property_ids": list(request.failed_property_ids),
        "failure_signatures": list(request.failure_signatures),
        "search_budget_consumed": False,
        "verdicts": verdicts,
        "patch_operation_id": patch.get("operation_id"),
        "replay_agent_id": replay.get("agent_id") or replay.get("run_id"),
        "input_tokens": int(patch.get("input_tokens", 0) or 0)
        + int(replay.get("input_tokens", 0) or 0),
        "output_tokens": int(patch.get("output_tokens", 0) or 0)
        + int(replay.get("output_tokens", 0) or 0),
        "wall_seconds": _safe_cost(
            replay.get("wall_seconds", 0.0) or 0.0, "replay wall time"
        ),
        "costs": {
            "patch_provider_credits": patch_cost,
            "replay_provider_credits": replay_cost,
            "total_provider_credits": round(patch_cost + replay_cost, 12),
        },
        "error": _clip(replay.get("error") or replay.get("error_message"), 500),
    }


def repair_failed_execution(
    request: FailureRepairRequest,
    evidence: Mapping[str, Any],
    *,
    patcher,
    executor,
) -> dict[str, Any]:
    """Patch and replay one failed execution with crash-safe exactly-once boundaries."""

    if not isinstance(request, FailureRepairRequest):
        raise TypeError("repair request must be FailureRepairRequest")
    if package_hash(request.original_skill_dir) != request.original_skill_hash:
        raise ValueError("original skill package differs from repair request")
    if not isinstance(evidence, Mapping):
        raise ValueError("repair evidence must be an object")
    payload = evidence.get("reviser_payload")
    if (
        evidence.get("repair_id") != request.repair_id
        or not isinstance(payload, Mapping)
        or evidence.get("evidence_hash") != canonical_json_hash(payload)
    ):
        raise ValueError("repair evidence identity mismatch")
    output = request.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    final_path = output / "repair.json"
    if final_path.exists():
        final = _read_object(final_path, "repair result")
        receipt = _read_object(output / "receipt.json", "repair receipt")
        if (
            final.get("repair_id") != request.repair_id
            or receipt.get("result_hash") != canonical_json_hash(final)
            or receipt.get("repair_id") != request.repair_id
        ):
            raise ValueError("terminal repair receipt is inconsistent")
        return final

    start = {
        "schema": "skillrace-failure-repair-start/1",
        "repair_id": request.repair_id,
        "request": request.identity(),
        "evidence_hash": evidence["evidence_hash"],
    }
    start_path = output / "start.json"
    created_start = _write_or_verify(start_path, start, "repair start")
    patch_path = output / "patch.json"
    patch_root = output / "patch"
    if patch_path.exists():
        patch = _read_object(patch_path, "repair patch")
        if patch.get("repair_id") != request.repair_id:
            raise ValueError("repair patch identity mismatch")
        patched_skill = (
            validate_skill_package(output / patch["skill_path"])
            if patch.get("status") == "completed"
            else None
        )
    else:
        if not created_start:
            raise UncertainRepairOutcomeError("repair patch outcome is unknown")
        patch_root.mkdir(parents=True, exist_ok=True)
        try:
            raw_patch = patcher(request, evidence, patch_root)
        except Exception as error:  # ordinary provider failure is terminal evidence
            raw_patch = {"status": "error", "error": type(error).__name__}
        patch, patched_skill = _normalize_patch_result(raw_patch, request, patch_root)
        _write_or_verify(patch_path, patch, "repair patch")
    if patch.get("status") != "completed" or patched_skill is None:
        replay = {"status": "error", "error": patch.get("error") or "patch failed"}
    else:
        replay_start = {
            "schema": "skillrace-failure-replay-start/1",
            "repair_id": request.repair_id,
            "case_dir_hash": canonical_json_hash({"case": str(request.case_dir)}),
            "patched_skill_hash": patch["skill_hash"],
        }
        replay_start_path = output / "replay-start.json"
        created_replay = _write_or_verify(
            replay_start_path, replay_start, "repair replay start"
        )
        replay_path = output / "result.json"
        if replay_path.exists():
            replay = _read_object(replay_path, "repair replay result")
        else:
            if not created_replay:
                raise UncertainRepairOutcomeError("repair replay outcome is unknown")
            replay_dir = output / "replay"
            replay_dir.mkdir(parents=True, exist_ok=True)
            try:
                raw_replay = executor(request, patched_skill, replay_dir)
            except Exception as error:  # ordinary execution failure is recorded
                raw_replay = {"status": "error", "error": type(error).__name__}
            replay = dict(raw_replay) if isinstance(raw_replay, Mapping) else {
                "status": "error",
                "error": "executor returned no object",
            }
            _write_or_verify(replay_path, replay, "repair replay result")
    final = _final_result(request, patch, replay)
    _write_or_verify(output / "result.json", replay, "repair replay result")
    _write_or_verify(final_path, final, "repair result")
    receipt = {
        "schema": "skillrace-failure-repair-receipt/1",
        "repair_id": request.repair_id,
        "start_hash": file_hash(start_path),
        "patch_hash": file_hash(patch_path),
        "replay_start_hash": (
            file_hash(output / "replay-start.json")
            if (output / "replay-start.json").exists()
            else None
        ),
        "replay_result_hash": file_hash(output / "result.json"),
        "result_hash": canonical_json_hash(final),
    }
    _write_or_verify(output / "receipt.json", receipt, "repair receipt")
    return final


def validate_repair_ledger(path: str | pathlib.Path) -> dict[str, Any]:
    """Recursively validate one campaign's per-failure repair ledger."""

    ledger_path = pathlib.Path(path).resolve()
    ledger = _read_object(ledger_path, "repair ledger")
    if ledger.get("schema") != "skillrace-failure-repairs/1":
        raise ValueError("unsupported repair ledger schema")
    repairs = ledger.get("repairs")
    if not isinstance(repairs, list):
        raise ValueError("repair ledger entries are malformed")
    if (
        ledger.get("failed_public_executions") != len(repairs)
        or ledger.get("repair_executions") != len(repairs)
        or ledger.get("repair_executions_counted_in_search_budget") is not False
    ):
        raise ValueError("repair ledger accounting is inconsistent")
    root = ledger_path.parent
    seen: set[str] = set()
    total_cost = 0.0
    for link in repairs:
        if not isinstance(link, Mapping):
            raise ValueError("repair ledger entry must be an object")
        repair_id = link.get("repair_id")
        if (
            not isinstance(repair_id, str)
            or not re.fullmatch(r"[0-9a-f]{24}", repair_id)
            or repair_id in seen
        ):
            raise ValueError("repair ledger contains invalid or duplicate repair ID")
        seen.add(repair_id)
        directory = root / repair_id
        evidence_path = directory / "evidence.json"
        result_path = directory / "repair.json"
        receipt_path = directory / "receipt.json"
        if (
            file_hash(evidence_path) != link.get("evidence_file_hash")
            or file_hash(result_path) != link.get("result_file_hash")
            or file_hash(receipt_path) != link.get("receipt_file_hash")
        ):
            raise ValueError(f"repair artifact hash mismatch for {repair_id}")
        result = _read_object(result_path, "repair result")
        evidence = _read_object(evidence_path, "repair evidence")
        receipt = _read_object(receipt_path, "repair receipt")
        if (
            result.get("repair_id") != repair_id
            or result.get("status") != link.get("status")
            or result.get("status") not in REPAIR_STATUSES
            or result.get("execution_id") != link.get("execution_id")
            or result.get("attempt_id") != link.get("attempt_id")
            or result.get("search_budget_consumed") is not False
        ):
            raise ValueError(f"repair result identity mismatch for {repair_id}")
        payload = evidence.get("reviser_payload")
        if (
            evidence.get("schema") != REPAIR_EVIDENCE_SCHEMA
            or evidence.get("repair_id") != repair_id
            or not isinstance(payload, Mapping)
            or evidence.get("evidence_hash") != canonical_json_hash(payload)
        ):
            raise ValueError(f"repair evidence identity mismatch for {repair_id}")
        start_path = directory / "start.json"
        patch_path = directory / "patch.json"
        replay_start_path = directory / "replay-start.json"
        replay_result_path = directory / "result.json"
        if (
            receipt.get("schema") != "skillrace-failure-repair-receipt/1"
            or receipt.get("repair_id") != repair_id
            or receipt.get("start_hash") != file_hash(start_path)
            or receipt.get("patch_hash") != file_hash(patch_path)
            or receipt.get("replay_result_hash") != file_hash(replay_result_path)
            or receipt.get("result_hash") != canonical_json_hash(result)
            or receipt.get("replay_start_hash")
            != (file_hash(replay_start_path) if replay_start_path.exists() else None)
        ):
            raise ValueError(f"repair receipt/result hash mismatch for {repair_id}")
        start = _read_object(start_path, "repair start")
        patch = _read_object(patch_path, "repair patch")
        replay = _read_object(replay_result_path, "repair replay result")
        if (
            start.get("schema") != "skillrace-failure-repair-start/1"
            or start.get("repair_id") != repair_id
            or start.get("evidence_hash") != evidence.get("evidence_hash")
            or patch.get("schema") != "skillrace-failure-repair-patch/1"
            or patch.get("repair_id") != repair_id
            or not isinstance(replay, Mapping)
        ):
            raise ValueError(f"repair recursive artifact identity mismatch for {repair_id}")
        costs = result.get("costs")
        if not isinstance(costs, Mapping):
            raise ValueError(f"repair cost is missing for {repair_id}")
        total_cost += _safe_cost(costs.get("total_provider_credits"), "repair total cost")
    costs = ledger.get("costs")
    if (
        not isinstance(costs, Mapping)
        or abs(_safe_cost(costs.get("total_provider_credits"), "ledger repair cost") - total_cost)
        > 1e-9
    ):
        raise ValueError("repair ledger total cost is inconsistent")
    return ledger


def repair_campaign_failures(
    campaign: Mapping[str, Any],
    *,
    skill_name: str,
    original_skill_dir: str | pathlib.Path,
    campaign_root: str | pathlib.Path,
    output_root: str | pathlib.Path,
    patcher=None,
    executor=None,
    job_runner=None,
    evidence_max_bytes: int,
) -> dict[str, Any]:
    """Patch/replay every raw failed public execution and publish one ledger."""

    if job_runner is None:
        if not callable(patcher) or not callable(executor):
            raise TypeError("repair campaign requires patcher and executor callables")
    elif not callable(job_runner):
        raise TypeError("repair campaign job_runner must be callable")
    elif patcher is not None or executor is not None:
        raise ValueError("confined job_runner may not be combined with patcher/executor")

    output = pathlib.Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    ledger_path = output / "repairs.json"
    source_hash = canonical_json_hash(campaign)
    original_hash = package_hash(original_skill_dir)
    if ledger_path.exists():
        ledger = validate_repair_ledger(ledger_path)
        if (
            ledger.get("source_campaign_hash") != source_hash
            or ledger.get("original_skill_hash") != original_hash
            or ledger.get("evidence_max_bytes") != evidence_max_bytes
        ):
            raise ValueError("repair ledger input identity mismatch")
        return ledger
    requests = select_failure_repairs(
        campaign,
        skill_name=skill_name,
        original_skill_dir=original_skill_dir,
        campaign_root=campaign_root,
        output_root=output,
        phase="public",
    )
    links: list[dict[str, Any]] = []
    patch_cost = replay_cost = 0.0
    for request in requests:
        evidence = build_repair_evidence(
            campaign,
            request,
            max_bytes=evidence_max_bytes,
        )
        request.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = request.output_dir / "evidence.json"
        _write_or_verify(evidence_path, evidence, "repair evidence")
        result = (
            job_runner(request, evidence)
            if job_runner is not None
            else repair_failed_execution(
                request,
                evidence,
                patcher=patcher,
                executor=executor,
            )
        )
        if (
            not isinstance(result, Mapping)
            or result.get("schema") != "skillrace-failure-repair-result/1"
            or result.get("repair_id") != request.repair_id
        ):
            raise ValueError("repair job returned a malformed terminal result")
        costs = result["costs"]
        patch_cost += _safe_cost(costs["patch_provider_credits"], "patch cost")
        replay_cost += _safe_cost(costs["replay_provider_credits"], "replay cost")
        links.append(
            {
                "repair_id": request.repair_id,
                "execution_id": request.execution_id,
                "attempt_id": request.attempt_id,
                "candidate_id": request.candidate_id,
                "status": result["status"],
                "evidence_file_hash": file_hash(evidence_path),
                "result_file_hash": file_hash(request.output_dir / "repair.json"),
                "receipt_file_hash": file_hash(request.output_dir / "receipt.json"),
            }
        )
    ledger = {
        "schema": "skillrace-failure-repairs/1",
        "method": campaign.get("method"),
        "skill_name": skill_name,
        "source_campaign_hash": source_hash,
        "original_skill_hash": original_hash,
        "evidence_max_bytes": evidence_max_bytes,
        "search_agent_executions": campaign.get("counted_executions"),
        "failed_public_executions": len(requests),
        "repair_executions": len(requests),
        "repair_executions_counted_in_search_budget": False,
        "repairs": links,
        "costs": {
            "patch_provider_credits": round(patch_cost, 12),
            "replay_provider_credits": round(replay_cost, 12),
            "total_provider_credits": round(patch_cost + replay_cost, 12),
        },
    }
    _write_or_verify(ledger_path, ledger, "repair ledger")
    return validate_repair_ledger(ledger_path)


def make_model_patcher(
    *,
    model: str,
    chat_fn=chat,
    temperature: float = 0.0,
    reasoning: bool = True,
    max_tokens: int = 4000,
):
    """Create the one frozen-model patcher used for every method's repair request."""

    if not isinstance(model, str) or not model or len(model) > 128:
        raise ValueError("repair patch model must be bounded text")
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
        raise ValueError("repair patch temperature must be numeric")
    if not 0 <= float(temperature) <= 2:
        raise ValueError("repair patch temperature is out of bounds")
    if not isinstance(reasoning, bool):
        raise ValueError("repair patch reasoning must be boolean")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError("repair patch max_tokens must be positive")
    if chat_fn is not chat and not is_nonproduction_chat_fixture(chat_fn):
        raise ValueError(
            "custom repair chat requires the explicit nonproduction fixture boundary"
        )
    config = {
        "model": model,
        "temperature": float(temperature),
        "reasoning": reasoning,
        "max_tokens": max_tokens,
        "prompt_version": "skillrace-per-failure-repair/1",
    }

    def patcher(
        request: FailureRepairRequest,
        evidence: Mapping[str, Any],
        patch_root: pathlib.Path,
    ) -> dict[str, Any]:
        source = validate_skill_package(request.original_skill_dir)
        if package_hash(source) != request.original_skill_hash:
            raise ValueError("repair patch source differs from request")
        payload = evidence.get("reviser_payload")
        if (
            not isinstance(payload, Mapping)
            or evidence.get("evidence_hash") != canonical_json_hash(payload)
        ):
            raise ValueError("repair patch evidence identity mismatch")
        current = (source / "SKILL.md").read_text(encoding="utf-8")
        evidence_json = canonical_json_bytes(payload).decode("utf-8")
        user_prompt = (
            "CURRENT SKILL.md:\n---\n"
            + current
            + "---\n\nFAILED-EXECUTION EVIDENCE (canonical JSON):\n"
            + "<repair-evidence>\n"
            + evidence_json
            + "\n</repair-evidence>\n\nOutput only the complete revised SKILL.md."
        )
        start_identity = {
            "schema": "skillrace-failure-repair-patch-start/1",
            "request": request.identity(),
            "evidence_hash": evidence["evidence_hash"],
            "system_prompt_hash": hashlib.sha256(
                REPAIR_PATCH_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "user_prompt_hash": hashlib.sha256(user_prompt.encode("utf-8")).hexdigest(),
            "model_config": config,
        }
        operation_id = f"repair.patch.{canonical_json_hash(start_identity)}"
        messages = [
            {"role": "system", "content": REPAIR_PATCH_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        expected_request = chat_request_identity(
            messages,
            model=model,
            temperature=float(temperature),
            max_tokens=max_tokens,
            reasoning=reasoning,
        )
        response = chat_fn(
            messages,
            model=model,
            temperature=float(temperature),
            reasoning=reasoning,
            max_tokens=max_tokens,
            tag="repair.patch",
            skill=request.skill_name,
            operation_id=operation_id,
        )
        validate_chat_result(
            response,
            expected_model=model,
            expected_operation_id=operation_id,
            expected_request_identity=expected_request,
            expected_tag="repair.patch",
            expected_skill=request.skill_name,
        )
        revised = normalize_revised_skill(response["content"])
        patch_root = pathlib.Path(patch_root).resolve()
        patch_root.mkdir(parents=True, exist_ok=True)
        skill_dir = copy_base_skill_package(source, patch_root / "skill")
        atomic_write_text(skill_dir / "SKILL.md", revised)
        validate_skill_package(skill_dir)
        provenance = patch_root / "provenance"
        provenance.mkdir()
        atomic_write_text(provenance / "raw-response.txt", response["content"])
        atomic_write_json(provenance / "model-call-terminal.json", response["journal_terminal_receipt"])
        atomic_write_json(
            provenance / "model-call-operation-terminal.json",
            response["journal_call_terminal_receipt"],
        )
        record = {
            "schema": "skillrace-failure-repair-patch-provenance/1",
            "repair_id": request.repair_id,
            "operation_id": response["operation_id"],
            "model_config": config,
            "start_identity": start_identity,
            "original_skill_hash": request.original_skill_hash,
            "evidence_hash": evidence["evidence_hash"],
            "revised_skill_hash": package_hash(skill_dir),
            "raw_response_hash": file_hash(provenance / "raw-response.txt"),
            "journal_terminal_hash": file_hash(
                provenance / "model-call-terminal.json"
            ),
            "journal_call_terminal_hash": file_hash(
                provenance / "model-call-operation-terminal.json"
            ),
            "input_tokens": int(response["usage"]["prompt_tokens"]),
            "output_tokens": int(response["usage"]["completion_tokens"]),
            "cost_provider_credits": float(response["cost_provider_credits"] or 0.0),
        }
        atomic_write_json(provenance / "patch.json", record)
        return {
            "status": "completed",
            "skill_dir": str(skill_dir),
            "operation_id": response["operation_id"],
            "input_tokens": record["input_tokens"],
            "output_tokens": record["output_tokens"],
            "cost_provider_credits": record["cost_provider_credits"],
        }

    return patcher


def make_replay_executor(
    *,
    model: str,
    wall_clock: int,
    run_agent_fn=None,
    check_run_fn=None,
):
    """Create the shared exact-case runner used by every repair method.

    A repair replay changes only the mounted skill package.  It reuses the saved
    public case, agent model, wall-clock limit, runner, and property checker.
    """

    if not isinstance(model, str) or not model or len(model) > 128:
        raise ValueError("repair replay model must be bounded text")
    if (
        isinstance(wall_clock, bool)
        or not isinstance(wall_clock, int)
        or wall_clock <= 0
    ):
        raise ValueError("repair replay wall_clock must be a positive integer")
    if run_agent_fn is None or check_run_fn is None:
        from .loop import check_run as default_check_run
        from .loop import run_agent as default_run_agent

        run_agent_fn = run_agent_fn or default_run_agent
        check_run_fn = check_run_fn or default_check_run
    if not callable(run_agent_fn) or not callable(check_run_fn):
        raise TypeError("repair replay runner and checker must be callable")

    def executor(
        request: FailureRepairRequest,
        patched_skill_dir: str | pathlib.Path,
        replay_dir: str | pathlib.Path,
    ) -> dict[str, Any]:
        if not isinstance(request, FailureRepairRequest):
            raise TypeError("repair replay request must be FailureRepairRequest")
        case = request.case_dir.resolve()
        if not case.is_dir():
            raise ValueError("repair replay requires the saved public case directory")
        skill = validate_skill_package(patched_skill_dir)
        replay = pathlib.Path(replay_dir).resolve()
        returncode, runner_tail, manifest = run_agent_fn(
            case,
            replay,
            model,
            wall_clock,
            skill,
        )
        manifest = dict(manifest) if isinstance(manifest, Mapping) else {}
        recorded_model = manifest.get("model")
        if recorded_model is not None and recorded_model != model:
            raise ValueError("repair replay run manifest records a different model")
        termination = manifest.get("termination")
        termination = termination if isinstance(termination, Mapping) else {}
        reason = termination.get("reason")
        if reason == "timeout" or returncode == 124:
            status = "timeout"
            verdicts: list[dict[str, Any]] = []
        elif returncode != 0:
            status = "error"
            verdicts = []
        else:
            raw_verdicts, checker_tail, checker_returncode = check_run_fn(replay, model)
            verdicts = (
                [dict(row) for row in raw_verdicts if isinstance(row, Mapping)]
                if isinstance(raw_verdicts, list)
                else []
            )
            status = "completed" if checker_returncode == 0 else "error"
            if status == "error" and not runner_tail:
                runner_tail = "\n".join(str(item) for item in checker_tail)
        cost_path = replay / "cost.json"
        cost = _read_object(cost_path, "repair replay cost")
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
            raise ValueError("repair replay token counts must be non-negative integers")
        result = {
            "status": status,
            "verdicts": verdicts,
            "agent_id": manifest.get("run_id"),
            "input_tokens": incoming,
            "output_tokens": outgoing,
            "cost_provider_credits": _safe_cost(
                cost.get("cost_provider_credits", cost.get("provider_credits", cost.get("price_provider_credits", 0.0)))
                or 0.0,
                "repair replay cost",
            ),
        }
        if status == "error":
            result["error"] = _clip(runner_tail, 500)
        return result

    return executor
