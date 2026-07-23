"""Crash-recoverable, equal-budget campaign state machine.

The engine is deliberately ignorant of Docker, Pi, and property checking.  A real
executor owns those stages and returns one JSON result; tests can inject a tiny
executor. Durable records make recovery unambiguous:

``proposal.json`` -> ``receipt.json`` -> ``cleanup.intent.json`` ->
``cleanup.json`` -> ``fold.json``

The executor receipt is published before campaign state changes.  Consequently a
resume can consume a completed execution without calling the executor again.  A
counted receipt remains ``pending_fold`` until its method-specific feedback has a
durable fold receipt and generator snapshot.
"""

from __future__ import annotations

import copy
import contextlib
import inspect
import json
import pathlib
import re
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from .campaign_protocol import CampaignProtocol
from .closeai import OutcomeUnknownError
from .io_utils import atomic_write_json, canonical_json_hash
from .parallel_campaign import (
    ParallelReducer,
    WorkerJob,
    freeze_adaptive_state,
    make_reservations,
    run_epoch,
)
from .resource_pool import ResourcePool


_ATTEMPT_RE = re.compile(r"e(?P<execution>[0-9]{4})-a(?P<attempt>[0-9]{2})\Z")
_CANDIDATE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_LIFECYCLE_RECORDS = {
    "started": ("external.started.json", "campaign-external-started/1"),
    "external-terminal": ("external.terminal.json", "campaign-external-terminal/1"),
    "executor-terminal": ("executor.terminal.json", "campaign-executor-terminal/1"),
}
_UNSET_PROPOSAL = object()


class CleanupRecoveryError(RuntimeError):
    """Cleanup intent exists but its postcondition cannot currently be measured."""


def _json_copy(value: Any, *, label: str) -> Any:
    """Copy through JSON so persisted state cannot contain aliases or odd objects."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be JSON serializable: {error}") from error


def _read_json(path: pathlib.Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"malformed {label} at {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"malformed {label} at {path}: expected a JSON object")
    return value


def _publish_immutable(path: pathlib.Path, value: dict[str, Any], *, label: str) -> str:
    """Create an immutable JSON record, accepting only byte-equivalent replays."""
    value = _json_copy(value, label=label)
    digest = canonical_json_hash(value)
    if path.exists():
        existing = _read_json(path, label=label)
        if canonical_json_hash(existing) != digest:
            raise ValueError(f"conflicting immutable {label} at {path}")
        return digest
    atomic_write_json(path, value)
    return digest


def _default_image_remover(image: str) -> None:
    process = subprocess.run(
        ["docker", "image", "rm", "-f", image],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if process.returncode != 0:
        output = (process.stdout + process.stderr).strip()
        # Docker reports an already-absent image as a nonzero result.  Absence is
        # already the desired postcondition, so it is not a cleanup failure.
        if "No such image" not in output and "not found" not in output.lower():
            raise RuntimeError(output[-500:] or f"docker image rm exited {process.returncode}")


def _default_image_exists(image: str) -> bool:
    process = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if process.returncode == 0:
        return True
    output = (process.stdout + process.stderr).strip()
    if "No such image" in output or "not found" in output.lower():
        return False
    raise CleanupRecoveryError(
        output[-500:] or f"docker image inspect exited {process.returncode}"
    )


def _cleanup_intent_path(receipt_path: pathlib.Path) -> pathlib.Path:
    return receipt_path.with_name(
        f"{receipt_path.stem}.intent{receipt_path.suffix}"
    )


def _cleanup_attempts_path(receipt_path: pathlib.Path) -> pathlib.Path:
    return receipt_path.with_name(f"{receipt_path.stem}.attempts")


def _load_cleanup_attempts(
    receipt_path: pathlib.Path,
    intent: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    root = _cleanup_attempts_path(receipt_path)
    if not root.exists():
        return []
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"malformed cleanup attempt evidence at {root}")
    attempts = []
    for ordinal, attempt_path in enumerate(sorted(root.glob("v*.json"))):
        if attempt_path.name != f"v{ordinal:04d}.json":
            raise ValueError(f"non-contiguous cleanup attempt evidence at {root}")
        report = _read_json(attempt_path, label="cleanup attempt receipt")
        _validate_cleanup_records(intent, report, candidate)
        if report.get("attempt_ordinal") != ordinal:
            raise ValueError("cleanup attempt receipt has conflicting ordinal")
        attempts.append(report)
    return attempts


def _validate_cleanup_attempt_history(
    receipt_path: pathlib.Path,
    intent: Mapping[str, Any],
    candidate: Mapping[str, Any],
    terminal: Mapping[str, Any],
) -> None:
    attempts = _load_cleanup_attempts(receipt_path, intent, candidate)
    if (
        terminal.get("attempt_count") != len(attempts)
        or terminal.get("attempt_history_hash") != canonical_json_hash(attempts)
    ):
        raise ValueError("cleanup terminal receipt has conflicting attempt history")


def _validate_cleanup_records(intent, report, candidate=None) -> None:
    if (
        not isinstance(intent, dict)
        or intent.get("schema") != "candidate-image-cleanup-intent/1"
        or intent.get("action") not in {"remove", "missing", "external", "base-image"}
        or not isinstance(intent.get("owned"), bool)
    ):
        raise ValueError("malformed cleanup intent")
    if (
        not isinstance(report, dict)
        or report.get("schema") != "candidate-image-cleanup/1"
        or report.get("candidate_id") != intent.get("candidate_id")
        or report.get("image") != intent.get("image")
        or report.get("owned") != intent.get("owned")
        or report.get("intent_hash") != canonical_json_hash(intent)
        or report.get("status") not in {"removed", "missing", "external", "base-image", "error"}
    ):
        raise ValueError("cleanup completion does not match intent")
    action = intent["action"]
    if action == "remove" and report["status"] not in {"removed", "error"}:
        raise ValueError("cleanup removal has an invalid completion status")
    if action != "remove" and report["status"] != action:
        raise ValueError("cleanup no-op has an invalid completion status")
    if candidate is not None and (
        intent.get("candidate_id") != candidate.get("candidate_id")
        or intent.get("image") != candidate.get("built_image")
        or intent.get("base_image") != candidate.get("base_image")
    ):
        raise ValueError("cleanup intent does not match candidate")


def _is_owned_candidate_image(candidate: Mapping[str, Any], image: str) -> bool:
    ownership = candidate.get("image_ownership")
    if isinstance(ownership, Mapping):
        ownership = ownership.get("owner") or ownership.get("kind")
    if ownership in {"external", "user", "borrowed", False}:
        return False
    if candidate.get("image_owned") is False:
        return False
    if ownership in {"campaign", "generator", "owned", True}:
        return True
    if candidate.get("image_owned") is True:
        return True
    candidate_id = candidate.get("candidate_id")
    # Backward-compatible ownership proof for images emitted by the one shared
    # realization pipeline.  Arbitrary user tags are never inferred as owned.
    return (
        isinstance(candidate_id, str)
        and image == f"skillrace/{candidate_id}:built"
    )


def cleanup_candidate_image(
    candidate: dict[str, Any] | None,
    *,
    remover: Callable[[str], Any] | None = None,
    image_exists: Callable[[str], bool] | None = None,
    receipt_path: str | pathlib.Path | None = None,
    fault_hook: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Remove one campaign-owned candidate image at most once.

    Passing ``receipt_path`` makes idempotence survive process crashes.  The helper
    never removes a base image or a tag explicitly marked external/user-owned, and
    cleanup errors are immutable versioned evidence rather than terminal completion.
    A later call first inspects image existence and can safely finish the cleanup.
    """
    candidate = candidate if isinstance(candidate, dict) else {}
    path = pathlib.Path(receipt_path) if receipt_path is not None else None
    if path is not None and path.exists():
        report = _read_json(path, label="cleanup receipt")
        if report.get("schema") != "candidate-image-cleanup/1":
            raise ValueError(f"malformed cleanup receipt at {path}: unsupported schema")
        intent_path = _cleanup_intent_path(path)
        intent = _read_json(intent_path, label="cleanup intent")
        _validate_cleanup_records(intent, report, candidate)
        if report.get("status") == "error":
            raise ValueError(
                "legacy terminal cleanup error cannot be treated as completion"
            )
        _validate_cleanup_attempt_history(path, intent, candidate, report)
        candidate["image_cleaned"] = report.get("status") in {
            "removed", "missing", "external", "base-image"
        }
        candidate["image_cleanup"] = copy.deepcopy(report)
        return report
    previous = candidate.get("image_cleanup")
    if candidate.get("image_cleaned") and isinstance(previous, dict):
        return copy.deepcopy(previous)

    image = candidate.get("built_image")
    base_image = candidate.get("base_image")
    intent: dict[str, Any] = {
        "schema": "candidate-image-cleanup-intent/1",
        "candidate_id": candidate.get("candidate_id"),
        "image": image if isinstance(image, str) else None,
        "base_image": base_image if isinstance(base_image, str) else None,
        "owned": False,
    }
    if not isinstance(image, str) or not image:
        planned_status = "missing"
    elif image == base_image:
        planned_status = "base-image"
    elif not _is_owned_candidate_image(candidate, image):
        planned_status = "external"
    else:
        intent["owned"] = True
        planned_status = "remove"
    intent["action"] = planned_status

    intent_path = _cleanup_intent_path(path) if path is not None else None
    existing_intent = intent_path is not None and intent_path.exists()
    if existing_intent:
        persisted_intent = _read_json(intent_path, label="cleanup intent")
        if canonical_json_hash(persisted_intent) != canonical_json_hash(intent):
            raise ValueError(f"conflicting immutable cleanup intent at {intent_path}")
    elif intent_path is not None:
        _publish_immutable(intent_path, intent, label="cleanup intent")
    if not existing_intent and fault_hook is not None:
        fault_hook("after_intent")

    prior_attempts = (
        _load_cleanup_attempts(path, intent, candidate)
        if path is not None
        else []
    )
    recovered = False
    removal_invoked = False
    error_text = None
    if planned_status == "remove":
        if existing_intent:
            try:
                still_exists = (image_exists or _default_image_exists)(image)
            except Exception as error:
                raise CleanupRecoveryError(
                    f"cleanup recovery inspect failed for {image}: {error}"
                ) from error
            if not still_exists:
                recovered = True
            else:
                removal_invoked = True
        else:
            removal_invoked = True
        if removal_invoked:
            try:
                (remover or _default_image_remover)(image)
            except Exception as error:  # evidence, never a silent cleanup success
                error_text = str(error)[:500]
            else:
                if fault_hook is not None:
                    fault_hook("after_remove")

    report: dict[str, Any] = {
        "schema": "candidate-image-cleanup/1",
        "candidate_id": candidate.get("candidate_id"),
        "image": image if isinstance(image, str) else None,
        "owned": intent["owned"],
        "intent_hash": canonical_json_hash(intent),
        "removal_invoked": removal_invoked,
        "recovered_after_intent": recovered,
        "attempt_ordinal": len(prior_attempts),
    }
    if error_text is not None:
        report["status"] = "error"
        report["error"] = error_text
    elif planned_status == "remove":
        report["status"] = "removed"
    else:
        report["status"] = planned_status

    if path is not None and planned_status == "remove":
        _validate_cleanup_records(intent, report, candidate)
        attempt_path = _cleanup_attempts_path(path) / f"v{len(prior_attempts):04d}.json"
        _publish_immutable(attempt_path, report, label="cleanup attempt receipt")
    if path is not None and report["status"] != "error":
        history = [*prior_attempts, *([report] if planned_status == "remove" else [])]
        report = {
            **report,
            "attempt_count": len(history),
            "attempt_history_hash": canonical_json_hash(history),
        }
        _validate_cleanup_records(intent, report, candidate)
        _publish_immutable(path, report, label="cleanup receipt")
    candidate["image_cleaned"] = report["status"] in {
        "removed", "missing", "external", "base-image"
    }
    candidate["image_cleanup"] = copy.deepcopy(report)
    return report


class CampaignEngine:
    """Injectable sequential engine with exactly-once durable execution receipts."""

    def __init__(
        self,
        *,
        protocol: CampaignProtocol,
        method: str,
        out_dir: str | pathlib.Path,
        generator: Any,
        executor: Any,
        bootstrap_generator: Any | None = None,
        skill: str | None = None,
        output_identity: str | None = None,
        image_remover: Callable[[str], Any] | None = None,
        image_inspector: Callable[[str], bool] | None = None,
        cleanup_fault_hook: Callable[[str], Any] | None = None,
        fault_hook: Callable[[str, dict[str, Any]], Any] | None = None,
        epoch_size: int = 1,
        resource_pool: ResourcePool | None = None,
    ) -> None:
        protocol.bootstrap_for(method)  # validate method immediately
        if protocol.budget > 10_000:
            raise ValueError("campaign budget exceeds deterministic execution ID range")
        if protocol.max_generation_attempts_per_execution > 100:
            raise ValueError("generation cap exceeds deterministic attempt ID range")
        if not isinstance(epoch_size, int) or isinstance(epoch_size, bool) or epoch_size <= 0:
            raise ValueError("epoch_size must be a positive integer")
        if protocol.status == "frozen" and epoch_size != 1:
            raise ValueError(
                "frozen headline campaigns are sequential within a cell; "
                "parallelism is scheduled across independent cells"
            )
        if protocol.bootstrap_for(method) and bootstrap_generator is None:
            raise ValueError(f"{method} requires a bootstrap generator")
        self.protocol = protocol
        self.method = method
        self.out_dir = pathlib.Path(out_dir)
        self.generator = generator
        self.bootstrap_generator = bootstrap_generator
        self.executor = executor
        self.skill = skill or getattr(generator, "skill", None) or "unspecified"
        self.output_identity = output_identity or str(self.out_dir.resolve())
        self.image_remover = image_remover or _default_image_remover
        self.image_inspector = image_inspector or _default_image_exists
        self.cleanup_fault_hook = cleanup_fault_hook
        self.fault_hook = fault_hook
        self.epoch_size = epoch_size
        self.resource_pool = resource_pool
        if self.epoch_size > 1 and self.resource_pool is None:
            self.resource_pool = ResourcePool(api=1, docker=1, agent=1)
        self.campaign_path = self.out_dir / "campaign.json"
        self.attempts_dir = self.out_dir / "attempts"
        self.state: dict[str, Any] | None = None

    # --------------------------------------------------------------- generator I/O

    @staticmethod
    def _snapshot(generator: Any | None) -> Any:
        if generator is None:
            return None
        if not hasattr(generator, "snapshot"):
            raise ValueError("campaign generators must implement snapshot()")
        return _json_copy(generator.snapshot(), label="generator snapshot")

    @staticmethod
    def _restore(
        generator: Any | None,
        snapshot: Any,
        *,
        forward_attempt_id: str | None = None,
    ) -> None:
        if generator is None:
            if snapshot is not None:
                raise ValueError("campaign contains unexpected bootstrap generator state")
            return
        if not hasattr(generator, "restore"):
            raise ValueError("campaign generators must implement restore()")
        copied = _json_copy(snapshot, label="generator snapshot")
        if forward_attempt_id is not None and hasattr(
            generator, "restore_for_pending_fold"
        ):
            generator.restore_for_pending_fold(copied, forward_attempt_id)
        else:
            generator.restore(copied)

    def _snapshots(self) -> tuple[Any, Any]:
        return self._snapshot(self.generator), self._snapshot(self.bootstrap_generator)

    def _restore_snapshots(
        self,
        main: Any,
        bootstrap: Any,
        *,
        forward_attempt_id: str | None = None,
    ) -> None:
        self._restore(
            self.generator, main, forward_attempt_id=forward_attempt_id
        )
        self._restore(self.bootstrap_generator, bootstrap)

    # ---------------------------------------------------------------- state I/O

    def _new_state(self) -> dict[str, Any]:
        main, bootstrap = self._snapshots()
        return {
            "schema": "campaign/2",
            "protocol_id": self.protocol.protocol_id,
            "protocol_hash": self.protocol.hash,
            "protocol": _json_copy(self.protocol.raw, label="campaign protocol"),
            "method": self.method,
            "skill": self.skill,
            "output_identity": self.output_identity,
            "budget": self.protocol.budget,
            "bootstrap_count": self.protocol.bootstrap_for(self.method),
            "seed_count": self.protocol.bootstrap_for(self.method),
            "configured_bootstrap_count": self.protocol.bootstrap_count,
            "allocation": self.protocol.allocation_for(self.method),
            "model": self.protocol.model,
            "agent_model": self.protocol.model,
            "greybox_level": (
                self.protocol.greybox_level if self.method == "greybox" else None
            ),
            "max_pre_agent_attempts": (
                self.protocol.max_generation_attempts_per_execution
            ),
            "counted_executions": 0,
            "attempts": [],
            "iterations": [],
            "generator_state": main,
            "bootstrap_generator_state": bootstrap,
            "pending_fold": None,
            "folded_attempt_ids": [],
            "status": "running",
            "complete": False,
            "stop_reason": None,
            "epoch_size": self.epoch_size,
            "next_epoch": 0,
            "next_execution_ordinal": 0,
            "active_epoch": None,
            "consecutive_parallel_pre_agent_failures": 0,
        }

    def _validate_identity(self, state: Mapping[str, Any]) -> None:
        expected = {
            "protocol hash": (state.get("protocol_hash"), self.protocol.hash),
            "method": (state.get("method"), self.method),
            "skill": (state.get("skill"), self.skill),
            "output identity": (state.get("output_identity"), self.output_identity),
            "epoch size": (state.get("epoch_size", 1), self.epoch_size),
        }
        for label, (actual, wanted) in expected.items():
            if actual != wanted:
                raise ValueError(
                    f"campaign {label} mismatch: found {actual!r}, expected {wanted!r}"
                )

    def _validate_state(self, state: dict[str, Any]) -> None:
        if state.get("schema") != "campaign/2":
            raise ValueError("unsupported or malformed campaign state schema")
        self._validate_identity(state)
        embedded_protocol = state.get("protocol")
        if (
            not isinstance(embedded_protocol, dict)
            or canonical_json_hash(embedded_protocol) != state.get("protocol_hash")
        ):
            raise ValueError("embedded protocol does not match protocol_hash")
        attempts = state.get("attempts")
        iterations = state.get("iterations")
        if not isinstance(attempts, list) or not isinstance(iterations, list):
            raise ValueError("malformed campaign attempts/iterations")
        if state.get("counted_executions") != len(iterations):
            raise ValueError("campaign counted execution total is inconsistent")
        seen_attempts: set[str] = set()
        for record in attempts:
            if not isinstance(record, dict) or not _ATTEMPT_RE.fullmatch(
                str(record.get("attempt_id", ""))
            ):
                raise ValueError("malformed campaign attempt record")
            attempt_id = record["attempt_id"]
            if attempt_id in seen_attempts:
                raise ValueError(f"duplicate campaign attempt {attempt_id}")
            seen_attempts.add(attempt_id)
        iteration_ids = [item.get("execution_id") for item in iterations]
        if self.epoch_size == 1:
            expected_execution_ids = [f"e{i:04d}" for i in range(len(iterations))]
            if iteration_ids != expected_execution_ids:
                raise ValueError("campaign iterations are not a contiguous execution prefix")
        elif len(iteration_ids) != len(set(iteration_ids)) or any(
            not isinstance(value, str) or re.fullmatch(r"e[0-9]{4}", value) is None
            for value in iteration_ids
        ):
            raise ValueError("parallel campaign iterations have invalid execution IDs")

    def _save(self) -> None:
        assert self.state is not None
        atomic_write_json(self.campaign_path, self.state)

    def _load_or_create(self) -> dict[str, Any]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.attempts_dir.mkdir(parents=True, exist_ok=True)
        if self.campaign_path.exists():
            state = _read_json(self.campaign_path, label="campaign state")
            self._validate_state(state)
        else:
            state = self._new_state()
            atomic_write_json(self.campaign_path, state)
        self.state = state
        self._audit_committed_artifacts()
        committed_ids = {record["attempt_id"] for record in state["attempts"]}
        uncommitted = []
        for proposal_path in sorted(self.attempts_dir.glob("*/proposal.json")):
            attempt_id = proposal_path.parent.name
            if attempt_id in committed_ids:
                continue
            match = _ATTEMPT_RE.fullmatch(attempt_id)
            if match is None:
                raise ValueError(f"malformed uncommitted proposal directory {attempt_id}")
            execution_id = f"e{int(match.group('execution')):04d}"
            if (
                self.epoch_size == 1
                and execution_id != f"e{state['counted_executions']:04d}"
            ):
                raise ValueError("uncommitted proposal is outside the next execution")
            uncommitted.append(
                self._load_proposal(proposal_path, execution_id, attempt_id)
            )
        if len(uncommitted) > 1 and self.epoch_size == 1:
            raise ValueError("multiple uncommitted campaign proposals")
        pending = state.get("pending_fold")
        if pending is not None:
            match = _ATTEMPT_RE.fullmatch(str(pending))
            if match is None:
                raise ValueError("malformed pending fold attempt ID")
            execution_id = f"e{int(match.group('execution')):04d}"
            fold_path = self._attempt_dir(pending) / "fold.json"
            if fold_path.exists():
                fold = self._load_fold(fold_path, execution_id, pending)
                self._restore_snapshots(
                    fold["generator_state"], fold["bootstrap_generator_state"]
                )
            else:
                self._restore_snapshots(
                    state["generator_state"],
                    state.get("bootstrap_generator_state"),
                    forward_attempt_id=pending,
                )
        elif uncommitted:
            # Parallel proposal hooks mutate one frozen batch before proposal
            # records are published.  A crash after any proposal therefore has a
            # durable post-batch snapshot which must be restored before planning
            # the missing records.  The original frozen hash is recovered below.
            proposal = uncommitted[-1]
            self._restore_snapshots(
                proposal["generator_state"], proposal["bootstrap_generator_state"]
            )
        else:
            self._restore_snapshots(
                state["generator_state"], state.get("bootstrap_generator_state")
            )
        return state

    # ------------------------------------------------------------- artifact schema

    def _identity_envelope(self, execution_id: str, attempt_id: str) -> dict[str, Any]:
        return {
            "protocol_hash": self.protocol.hash,
            "method": self.method,
            "skill": self.skill,
            "output_identity": self.output_identity,
            "execution_id": execution_id,
            "attempt_id": attempt_id,
        }

    def _validate_envelope(
        self,
        value: Mapping[str, Any],
        *,
        schema: str,
        execution_id: str,
        attempt_id: str,
        label: str,
    ) -> None:
        if value.get("schema") != schema:
            raise ValueError(f"malformed {label}: unsupported schema")
        expected = self._identity_envelope(execution_id, attempt_id)
        for field, wanted in expected.items():
            if value.get(field) != wanted:
                raise ValueError(f"conflicting {label} {field}")

    def _attempt_dir(self, attempt_id: str) -> pathlib.Path:
        return self.attempts_dir / attempt_id

    def _load_proposal(self, path: pathlib.Path, execution_id: str, attempt_id: str):
        proposal = _read_json(path, label="proposal")
        self._validate_envelope(
            proposal,
            schema="campaign-proposal/1",
            execution_id=execution_id,
            attempt_id=attempt_id,
            label="proposal",
        )
        candidate = proposal.get("candidate")
        if candidate is not None and not isinstance(candidate, dict):
            raise ValueError("malformed proposal candidate")
        if not isinstance(proposal.get("phase"), str):
            raise ValueError("malformed proposal phase")
        if "generator_state" not in proposal or "bootstrap_generator_state" not in proposal:
            raise ValueError("malformed proposal generator snapshots")
        return proposal

    def _load_receipt(self, path: pathlib.Path, execution_id: str, attempt_id: str):
        receipt = _read_json(path, label="receipt")
        self._validate_envelope(
            receipt,
            schema="campaign-attempt-receipt/1",
            execution_id=execution_id,
            attempt_id=attempt_id,
            label="receipt",
        )
        self._validate_result(receipt.get("result"), label="receipt result")
        recorded = receipt.get("lifecycle_journal", {})
        if not isinstance(recorded, dict):
            raise ValueError("malformed receipt lifecycle journal")
        actual = self._lifecycle_hashes(
            execution_id,
            attempt_id,
            receipt.get("candidate_id"),
        )
        if recorded != actual:
            raise ValueError("conflicting lifecycle journal bound to receipt")
        return receipt

    def _lifecycle_path(self, attempt_id: str, event: str) -> pathlib.Path:
        try:
            filename, _ = _LIFECYCLE_RECORDS[event]
        except KeyError as error:
            raise ValueError(f"unsupported execution lifecycle event {event!r}") from error
        return self._attempt_dir(attempt_id) / filename

    def _publish_lifecycle(
        self,
        event: str,
        evidence: Mapping[str, Any],
        candidate_id: str | None,
        execution_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        if not isinstance(evidence, Mapping):
            raise ValueError("execution lifecycle evidence must be an object")
        _, schema = _LIFECYCLE_RECORDS.get(event, (None, None))
        if schema is None:
            raise ValueError(f"unsupported execution lifecycle event {event!r}")
        record = {
            "schema": schema,
            **self._identity_envelope(execution_id, attempt_id),
            "candidate_id": candidate_id,
        }
        if event == "started":
            record["evidence"] = _json_copy(dict(evidence), label="started evidence")
        else:
            result = evidence.get("result")
            record["result"] = _json_copy(
                self._validate_result(result, label=f"{event} result"),
                label=f"{event} result",
            )
        _publish_immutable(
            self._lifecycle_path(attempt_id, event),
            record,
            label=f"{event} lifecycle record",
        )
        return record

    def _load_lifecycle(
        self,
        event: str,
        execution_id: str,
        attempt_id: str,
        candidate_id: str | None,
    ) -> dict[str, Any] | None:
        path = self._lifecycle_path(attempt_id, event)
        if not path.exists():
            return None
        record = _read_json(path, label=f"{event} lifecycle record")
        _, schema = _LIFECYCLE_RECORDS[event]
        self._validate_envelope(
            record,
            schema=schema,
            execution_id=execution_id,
            attempt_id=attempt_id,
            label=f"{event} lifecycle record",
        )
        if record.get("candidate_id") != candidate_id:
            raise ValueError(f"conflicting {event} lifecycle candidate identity")
        if event == "started":
            if not isinstance(record.get("evidence"), dict):
                raise ValueError("malformed started lifecycle evidence")
        else:
            self._validate_result(record.get("result"), label=f"{event} result")
        return record

    def _lifecycle_hashes(
        self,
        execution_id: str,
        attempt_id: str,
        candidate_id: str | None,
    ) -> dict[str, str]:
        hashes = {}
        for event in _LIFECYCLE_RECORDS:
            record = self._load_lifecycle(
                event, execution_id, attempt_id, candidate_id
            )
            if record is not None:
                hashes[event] = canonical_json_hash(record)
        return hashes

    def _recover_lifecycle_result(
        self,
        execution_id: str,
        attempt_id: str,
        candidate_id: str | None,
    ) -> dict[str, Any] | None:
        for event in ("executor-terminal", "external-terminal"):
            record = self._load_lifecycle(
                event, execution_id, attempt_id, candidate_id
            )
            if record is not None:
                result = _json_copy(record["result"], label=f"recovered {event} result")
                result["lifecycle_recovery"] = event
                if event == "external-terminal":
                    result["cost_accounting"] = "unknown-nonzero-possible"
                    result["unrecorded_cost_possible"] = True
                return result
        started = self._load_lifecycle(
            "started", execution_id, attempt_id, candidate_id
        )
        if started is None:
            return None
        evidence = started["evidence"]
        result = {
            "agent_started": None,
            "launch_committed": None,
            "status": "external-outcome-indeterminate",
            "generation_status": "generated",
            "infrastructure_status": "external_state_indeterminate",
            "runner_status": "external-outcome-indeterminate",
            "oracle_status": "not_run",
            "violated": [],
            "inconclusive": [],
            "lifecycle_recovery": "started-only",
            "cost_accounting": "unknown-nonzero-possible",
            "unrecorded_cost_possible": True,
            "consume_budget_conservatively": True,
            "budget_accounting_reason": "launch-state-indeterminate",
            "error": (
                "the external action started but no terminal evidence was durably "
                "published; it was conservatively counted and was not rerun"
            ),
        }
        if isinstance(evidence.get("run_dir"), str):
            result["run_dir"] = evidence["run_dir"]
        return result

    def _load_fold(self, path: pathlib.Path, execution_id: str, attempt_id: str):
        fold = _read_json(path, label="fold receipt")
        self._validate_envelope(
            fold,
            schema="campaign-fold/1",
            execution_id=execution_id,
            attempt_id=attempt_id,
            label="fold receipt",
        )
        if fold.get("status") not in {"folded", "error"}:
            raise ValueError("malformed fold receipt status")
        if "generator_state" not in fold or "bootstrap_generator_state" not in fold:
            raise ValueError("malformed fold receipt generator snapshots")
        return fold

    def _audit_committed_artifacts(self) -> None:
        assert self.state is not None
        for record in self.state["attempts"]:
            attempt_id = record["attempt_id"]
            execution_id = record["execution_id"]
            directory = self._attempt_dir(attempt_id)
            proposal = self._load_proposal(
                directory / "proposal.json", execution_id, attempt_id
            )
            if canonical_json_hash(proposal) != record.get("proposal_hash"):
                raise ValueError(f"conflicting immutable proposal for {attempt_id}")
            receipt = self._load_receipt(directory / "receipt.json", execution_id, attempt_id)
            if canonical_json_hash(receipt) != record.get("receipt_hash"):
                raise ValueError(f"conflicting immutable receipt for {attempt_id}")
            cleanup = _read_json(directory / "cleanup.json", label="cleanup receipt")
            intent = _read_json(
                directory / "cleanup.intent.json", label="cleanup intent"
            )
            _validate_cleanup_records(intent, cleanup, record.get("candidate") or {})
            _validate_cleanup_attempt_history(
                directory / "cleanup.json",
                intent,
                record.get("candidate") or {},
                cleanup,
            )
            if canonical_json_hash(intent) != record.get("cleanup_intent_hash"):
                raise ValueError(f"conflicting immutable cleanup intent for {attempt_id}")
            if cleanup.get("intent_hash") != canonical_json_hash(intent):
                raise ValueError(f"cleanup receipt/intention mismatch for {attempt_id}")
            if canonical_json_hash(cleanup) != record.get("cleanup_hash"):
                raise ValueError(f"conflicting immutable cleanup receipt for {attempt_id}")
            if record.get("fold_hash"):
                fold = self._load_fold(directory / "fold.json", execution_id, attempt_id)
                if canonical_json_hash(fold) != record["fold_hash"]:
                    raise ValueError(f"conflicting immutable fold receipt for {attempt_id}")

    # --------------------------------------------------------------- proposal/run

    def _event(self, event: str, execution_id: str, attempt_id: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(
                event,
                {
                    "event": event,
                    "execution_id": execution_id,
                    "attempt_id": attempt_id,
                    "campaign_path": str(self.campaign_path),
                },
            )

    def _phase(self, ordinal: int) -> str:
        return (
            "bootstrap"
            if ordinal < self.protocol.bootstrap_for(self.method)
            else "explore"
        )

    def _proposal_generator(self, phase: str) -> Any:
        return self.bootstrap_generator if phase == "bootstrap" else self.generator

    @staticmethod
    def _normalise_proposal(value: Any, default_source: str) -> tuple[Any, str]:
        source = default_source
        if isinstance(value, tuple) and len(value) == 2:
            value, source = value
        if value is not None and not isinstance(value, dict):
            raise ValueError("generator propose() must return a candidate object or null")
        if value is not None and not _CANDIDATE_ID_RE.fullmatch(
            str(value.get("candidate_id", ""))
        ):
            raise ValueError(
                "candidate_id must be a safe 1-128 character identifier"
            )
        if not isinstance(source, str) or not source:
            raise ValueError("generator proposal source must be a nonempty string")
        return value, source

    def _create_proposal(
        self,
        execution_id: str,
        attempt_id: str,
        phase: str,
        *,
        reservation=None,
        epoch: int | None = None,
        frozen_state_hash: str | None = None,
        proposal_result: Any = _UNSET_PROPOSAL,
    ) -> dict[str, Any]:
        generator = self._proposal_generator(phase)
        candidate = None
        error = None
        source = "bootstrap" if phase == "bootstrap" else self.method
        try:
            supplied_error = None
            if proposal_result is _UNSET_PROPOSAL:
                resource_context = (
                    self.resource_pool.slots("api", "docker")
                    if self.resource_pool is not None
                    else contextlib.nullcontext()
                )
                with resource_context:
                    candidate, source = self._normalise_proposal(
                        generator.propose(), source
                    )
            else:
                if not isinstance(proposal_result, Mapping):
                    raise ValueError("epoch proposal result must be an object")
                proposed_source = proposal_result.get("source", source)
                candidate, source = self._normalise_proposal(
                    (proposal_result.get("candidate"), proposed_source), source
                )
                supplied_error = proposal_result.get("error")
                if candidate is not None and supplied_error is not None:
                    raise ValueError(
                        "epoch proposal cannot contain both a candidate and an error"
                    )
            if candidate is not None and reservation is not None:
                candidate = copy.deepcopy(candidate)
                original_id = candidate.get("candidate_id")
                candidate["candidate_id"] = reservation.candidate_id
                provenance = dict(candidate.get("provenance") or {})
                provenance.update(dict(reservation.provenance))
                provenance.update(
                    {
                        "generator_candidate_id": original_id,
                        "frozen_state_hash": frozen_state_hash,
                    }
                )
                candidate["provenance"] = provenance
                if candidate.get("built_image"):
                    candidate["image_ownership"] = "campaign"
            if candidate is None:
                if supplied_error is None:
                    error = {
                        "type": "GenerationFailure",
                        "reason": "generator-exhausted",
                        "message": "generator returned no candidate",
                    }
                elif isinstance(supplied_error, str):
                    error = {
                        "type": "GenerationFailure",
                        "reason": "generation-error",
                        "message": supplied_error[:500],
                    }
                elif isinstance(supplied_error, Mapping):
                    error = {
                        "type": str(
                            supplied_error.get("type") or "GenerationFailure"
                        )[:100],
                        "reason": str(
                            supplied_error.get("reason") or "generation-error"
                        )[:100],
                        "message": str(
                            supplied_error.get("message") or "generator failed"
                        )[:500],
                    }
                else:
                    raise ValueError("malformed epoch proposal generation error")
        except OutcomeUnknownError as exc:
            error = {
                "type": type(exc).__name__,
                "reason": "external-outcome-unknown",
                "message": str(exc)[:500],
            }
        except Exception as exc:
            error = {
                "type": type(exc).__name__,
                "reason": getattr(exc, "reason", "generation-error"),
                "message": str(exc)[:500],
            }
        main, bootstrap = self._snapshots()
        proposal = {
            "schema": "campaign-proposal/1",
            **self._identity_envelope(execution_id, attempt_id),
            "phase": phase,
            "source": source,
            "candidate": _json_copy(candidate, label="candidate") if candidate is not None else None,
            "generation_error": error,
            "epoch": epoch,
            "frozen_state_hash": frozen_state_hash,
            "generator_state": main,
            "bootstrap_generator_state": bootstrap,
        }
        directory = self._attempt_dir(attempt_id)
        directory.mkdir(parents=True, exist_ok=True)
        _publish_immutable(directory / "proposal.json", proposal, label="proposal")
        self._event("after_proposal", execution_id, attempt_id)
        return proposal

    @staticmethod
    def _validate_result(value: Any, *, label: str = "executor result") -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(f"malformed {label}: expected an object")
        if value.get("agent_started") is not None and not isinstance(
            value.get("agent_started"), bool
        ):
            raise ValueError(
                f"malformed {label}: agent_started must be boolean or null"
            )
        if not isinstance(value.get("status"), str) or not value["status"]:
            raise ValueError(f"malformed {label}: status must be a nonempty string")
        for field in ("violated", "inconclusive"):
            if field in value and not isinstance(value[field], list):
                raise ValueError(f"malformed {label}: {field} must be a list")
        cost_receipt = value.get("run_cost_receipt")
        if cost_receipt is not None:
            if (
                not isinstance(cost_receipt, dict)
                or cost_receipt.get("schema") != "run-cost-receipt/1"
                or not isinstance(cost_receipt.get("path"), str)
                or not isinstance(cost_receipt.get("cost"), dict)
                or cost_receipt.get("cost_hash")
                != canonical_json_hash(cost_receipt.get("cost"))
            ):
                raise ValueError(f"malformed {label}: invalid run cost receipt")
            on_disk = _read_json(
                pathlib.Path(cost_receipt["path"]), label="run cost receipt"
            )
            if canonical_json_hash(on_disk) != cost_receipt["cost_hash"]:
                raise ValueError(f"conflicting {label}: run cost receipt drift")
        return value

    def _execute_and_publish(
        self,
        proposal: dict[str, Any],
        execution_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        candidate = proposal["candidate"]
        if candidate is None:
            generation_error = proposal.get("generation_error") or {}
            if generation_error.get("reason") == "external-outcome-unknown":
                result = {
                    "agent_started": False,
                    "status": "external-outcome-indeterminate",
                    "generation_status": "generation_error",
                    "infrastructure_status": "external_state_indeterminate",
                    "oracle_status": "not_run",
                    "violated": [],
                    "inconclusive": [],
                    "cost_accounting": "unknown-nonzero-possible",
                    "unrecorded_cost_possible": True,
                    "stop_campaign": True,
                    "error": generation_error.get("message", "operation outcome is unknown"),
                }
            else:
                result = {
                    "agent_started": False,
                    "status": generation_error.get("reason", "generation-error"),
                    "generation_status": "generation_error",
                    "infrastructure_status": "not_started",
                    "oracle_status": "not_run",
                    "violated": [],
                    "inconclusive": [],
                    "error": generation_error.get("message", "generator returned no candidate"),
                }
        else:
            candidate_id = candidate.get("candidate_id")
            result = self._recover_lifecycle_result(
                execution_id, attempt_id, candidate_id
            )
            if result is None:
                execute = getattr(self.executor, "execute", self.executor)
                callback_failure: list[BaseException] = []

                def lifecycle(event, evidence):
                    try:
                        self._publish_lifecycle(
                            event,
                            evidence,
                            candidate_id,
                            execution_id,
                            attempt_id,
                        )
                        self._event(
                            f"after_{event.replace('-', '_')}",
                            execution_id,
                            attempt_id,
                        )
                    except BaseException as error:  # journal failures must abort
                        callback_failure.append(error)
                        raise

                try:
                    try:
                        signature = inspect.signature(execute)
                    except (TypeError, ValueError):
                        signature = None
                    accepts_lifecycle = signature is not None and (
                        "lifecycle" in signature.parameters
                        or any(
                            parameter.kind == parameter.VAR_KEYWORD
                            for parameter in signature.parameters.values()
                        )
                    )
                    if accepts_lifecycle:
                        result = execute(
                            candidate,
                            execution_id,
                            attempt_id,
                            lifecycle=lifecycle,
                        )
                    else:
                        result = execute(candidate, execution_id, attempt_id)
                except Exception as error:
                    if callback_failure and callback_failure[-1] is error:
                        raise
                    result = self._recover_lifecycle_result(
                        execution_id, attempt_id, candidate_id
                    )
                    if result is None:
                        result = {
                            "agent_started": False,
                            "status": "executor-infrastructure-error",
                            "generation_status": "generated",
                            "infrastructure_status": "executor_error",
                            "oracle_status": "not_run",
                            "violated": [],
                            "inconclusive": [],
                            "error": str(error)[:500],
                        }
                result = _json_copy(
                    self._validate_result(result), label="executor result"
                )
                self._publish_lifecycle(
                    "executor-terminal",
                    {"result": result},
                    candidate_id,
                    execution_id,
                    attempt_id,
                )
                self._event("after_executor_terminal", execution_id, attempt_id)
        result = _json_copy(
            self._validate_result(result), label="executor result"
        )
        returned_candidate = result.get("candidate")
        if isinstance(returned_candidate, dict):
            if returned_candidate.get("candidate_id") != candidate.get("candidate_id"):
                raise ValueError("executor returned a conflicting candidate identity")
        receipt = {
            "schema": "campaign-attempt-receipt/1",
            **self._identity_envelope(execution_id, attempt_id),
            "candidate_id": candidate.get("candidate_id") if candidate else None,
            "result": result,
            "lifecycle_journal": self._lifecycle_hashes(
                execution_id,
                attempt_id,
                candidate.get("candidate_id") if candidate else None,
            ),
        }
        path = self._attempt_dir(attempt_id) / "receipt.json"
        _publish_immutable(path, receipt, label="receipt")
        self._event("after_receipt", execution_id, attempt_id)
        return receipt

    # --------------------------------------------------------------- commit/fold

    def _ensure_cleanup(
        self,
        proposal: dict[str, Any],
        execution_id: str,
        attempt_id: str,
    ) -> tuple[dict[str, Any], str, str]:
        path = self._attempt_dir(attempt_id) / "cleanup.json"

        def cleanup_event(event):
            if self.cleanup_fault_hook is not None:
                self.cleanup_fault_hook(event)
            self._event(f"cleanup_{event}", execution_id, attempt_id)

        cleanup = cleanup_candidate_image(
            copy.deepcopy(proposal.get("candidate")),
            remover=self.image_remover,
            image_exists=self.image_inspector,
            receipt_path=path,
            fault_hook=cleanup_event,
        )
        if cleanup.get("status") == "error":
            raise CleanupRecoveryError(
                "candidate image cleanup failed; immutable attempt evidence was "
                "recorded and a resume must inspect and retry"
            )
        digest = canonical_json_hash(cleanup)
        intent = _read_json(
            self._attempt_dir(attempt_id) / "cleanup.intent.json",
            label="cleanup intent",
        )
        intent_digest = canonical_json_hash(intent)
        self._event("after_cleanup", execution_id, attempt_id)
        return cleanup, digest, intent_digest

    @staticmethod
    def _record_from(
        proposal: dict[str, Any],
        receipt: dict[str, Any],
        cleanup: dict[str, Any],
        cleanup_intent_hash: str,
        proposal_hash: str,
        receipt_hash: str,
        cleanup_hash: str,
    ) -> dict[str, Any]:
        result = receipt["result"]
        candidate = proposal.get("candidate") or {}
        started = result["agent_started"]
        conservative_budget = result.get("consume_budget_conservatively") is True
        consume_budget = started is True or conservative_budget
        record = {
            "i": int(receipt["execution_id"][1:]),
            "execution_id": receipt["execution_id"],
            "attempt_id": receipt["attempt_id"],
            "phase": proposal["phase"],
            "source": proposal["source"],
            "candidate_id": receipt.get("candidate_id"),
            "candidate": candidate,
            "provenance": candidate.get("provenance"),
            "generation_status": result.get("generation_status", "generated"),
            "infrastructure_status": result.get(
                "infrastructure_status", "ready" if started else "pre_agent_failure"
            ),
            "runner_status": result.get("runner_status", result["status"]),
            "oracle_status": result.get("oracle_status", "not_run"),
            "agent_started": started,
            "consume_budget": consume_budget,
            "consume_budget_conservatively": conservative_budget,
            "budget_accounting_reason": result.get("budget_accounting_reason"),
            "violated": result.get("violated", []),
            "inconclusive": result.get("inconclusive", []),
            "n_verdicts": result.get(
                "n_verdicts", len(result.get("violated", [])) + len(result.get("inconclusive", []))
            ),
            "run": result.get("run_dir", result.get("run")),
            "case": result.get("case_dir"),
            "status": result["status"],
            "classification": result.get("classification"),
            "cleanup": cleanup,
            "cleanup_intent_hash": cleanup_intent_hash,
            "proposal_hash": proposal_hash,
            "receipt_hash": receipt_hash,
            "cleanup_hash": cleanup_hash,
            "fold_status": "pending" if consume_budget else "not_applicable",
            "result": result,
        }
        if result.get("error"):
            record["error"] = result["error"]
        for field in (
            "runtime_integrity",
            "runtime_rejection",
            "runtime_error",
            "sanity",
            "sanity_status",
            "sanity_rejection",
            "sanity_error",
            "compile_cost_provider_credits",
            "runner_returncode",
            "runner_error",
            "termination",
            "oracle_error",
            "regrade",
            "reproducible",
            "seconds",
        ):
            if field in result:
                record[field] = result[field]
        return record

    def _committed_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        assert self.state is not None
        return next(
            (item for item in self.state["attempts"] if item["attempt_id"] == attempt_id),
            None,
        )

    def _commit_attempt(
        self,
        proposal: dict[str, Any],
        receipt: dict[str, Any],
        cleanup: dict[str, Any],
        cleanup_hash: str,
        cleanup_intent_hash: str,
    ) -> dict[str, Any]:
        assert self.state is not None
        attempt_id = receipt["attempt_id"]
        existing = self._committed_attempt(attempt_id)
        if existing is not None:
            return existing
        receipt_hash = canonical_json_hash(receipt)
        proposal_hash = canonical_json_hash(proposal)
        record = self._record_from(
            proposal,
            receipt,
            cleanup,
            cleanup_intent_hash,
            proposal_hash,
            receipt_hash,
            cleanup_hash,
        )
        self.state["attempts"].append(record)
        self.state["generator_state"] = proposal["generator_state"]
        self.state["bootstrap_generator_state"] = proposal["bootstrap_generator_state"]
        if record["consume_budget"]:
            self.state["pending_fold"] = attempt_id
        self._save()
        self._event("after_commit", receipt["execution_id"], attempt_id)
        return record

    @staticmethod
    def _fold_accepts_attempt_id(generator: Any) -> bool:
        try:
            signature = inspect.signature(generator.fold)
        except (TypeError, ValueError):
            return False
        return "attempt_id" in signature.parameters or any(
            parameter.kind == parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )

    def _call_fold(
        self,
        candidate: dict[str, Any],
        result: dict[str, Any],
        phase: str,
        attempt_id: str,
    ) -> Any:
        run_dir = result.get("run_dir", result.get("run")) or str(
            self._attempt_dir(attempt_id)
        )
        kwargs = {"phase": phase}
        if self._fold_accepts_attempt_id(self.generator):
            kwargs["attempt_id"] = attempt_id
        fold_candidate = copy.deepcopy(candidate)
        fold_candidate["_execution_result"] = copy.deepcopy(result)
        if result.get("case_dir") is not None:
            fold_candidate["case_dir"] = result["case_dir"]
        return self.generator.fold(fold_candidate, run_dir, **kwargs)

    def _finish_fold(
        self,
        proposal: dict[str, Any],
        receipt: dict[str, Any],
        *,
        restore_proposal_state: bool = True,
    ) -> None:
        assert self.state is not None
        attempt_id = receipt["attempt_id"]
        execution_id = receipt["execution_id"]
        record = self._committed_attempt(attempt_id)
        if record is None or not record["consume_budget"]:
            raise ValueError(f"cannot fold uncommitted/non-counted attempt {attempt_id}")
        if any(item["attempt_id"] == attempt_id for item in self.state["iterations"]):
            self.state["pending_fold"] = None
            return

        fold_path = self._attempt_dir(attempt_id) / "fold.json"
        if fold_path.exists():
            fold_receipt = self._load_fold(fold_path, execution_id, attempt_id)
        else:
            # State at commit is the post-proposal/pre-fold snapshot.  Reapplying a
            # fold after a crash is safe because adaptive generators key it by ID.
            if restore_proposal_state:
                self._restore_snapshots(
                    proposal["generator_state"],
                    proposal["bootstrap_generator_state"],
                    forward_attempt_id=attempt_id,
                )
            fold_result = None
            error = None
            status = "folded"
            try:
                fold_result = self._call_fold(
                    proposal["candidate"], receipt["result"], proposal["phase"], attempt_id
                )
            except Exception as exc:
                status = "error"
                error = str(exc)[:500]
            self._event("after_fold", execution_id, attempt_id)
            main, bootstrap = self._snapshots()
            classification = receipt["result"].get("classification")
            if classification is None and isinstance(fold_result, dict):
                classification = fold_result.get("classification")
            fold_receipt = {
                "schema": "campaign-fold/1",
                **self._identity_envelope(execution_id, attempt_id),
                "phase": proposal["phase"],
                "status": status,
                "classification": classification,
                "fold_result": _json_copy(fold_result, label="fold result"),
                "error": error,
                "generator_state": main,
                "bootstrap_generator_state": bootstrap,
            }
            _publish_immutable(fold_path, fold_receipt, label="fold receipt")
        self._event("after_fold_receipt", execution_id, attempt_id)

        fold_hash = canonical_json_hash(fold_receipt)
        self._restore_snapshots(
            fold_receipt["generator_state"],
            fold_receipt["bootstrap_generator_state"],
        )
        record["fold_status"] = fold_receipt["status"]
        record["fold_hash"] = fold_hash
        record["fold_error"] = fold_receipt.get("error")
        record["classification"] = fold_receipt.get("classification")
        record["fold_result"] = fold_receipt.get("fold_result")
        self.state["generator_state"] = fold_receipt["generator_state"]
        self.state["bootstrap_generator_state"] = fold_receipt[
            "bootstrap_generator_state"
        ]
        if attempt_id not in self.state["folded_attempt_ids"]:
            self.state["folded_attempt_ids"].append(attempt_id)
        self.state["iterations"].append(_json_copy(record, label="iteration record"))
        self.state["counted_executions"] = len(self.state["iterations"])
        self.state["pending_fold"] = None
        self._save()
        self._event("after_finalize", execution_id, attempt_id)

    # ------------------------------------------------------------------- driving

    def _parallel_campaign_id(self) -> str:
        return "/".join(
            (
                self.protocol.protocol_id,
                self.method,
                self.skill,
                canonical_json_hash({"output_identity": self.output_identity})[:16],
            )
        )

    def _plan_parallel_epoch(self) -> dict[str, Any]:
        assert self.state is not None
        epoch = int(self.state.get("next_epoch", 0))
        remaining = self.protocol.budget - self.state["counted_executions"]
        phase = self._phase(self.state["counted_executions"])
        if phase == "bootstrap":
            phase_remaining = (
                self.protocol.bootstrap_for(self.method)
                - self.state["counted_executions"]
            )
        else:
            phase_remaining = remaining
        capacity = min(
            self.epoch_size,
            remaining,
            phase_remaining,
            self.resource_pool.agent_capacity if self.resource_pool is not None else self.epoch_size,
        )
        start = int(self.state.get("next_execution_ordinal", 0))
        coordinates = [
            (f"e{start + slot:04d}", f"e{start + slot:04d}-a00")
            for slot in range(capacity)
        ]
        reservations = make_reservations(
            self._parallel_campaign_id(), coordinates, epoch=epoch
        )
        main_before, bootstrap_before = self._snapshots()
        frozen = freeze_adaptive_state(
            tree_version=epoch,
            artifacts={
                "generator_state": main_before,
                "bootstrap_generator_state": bootstrap_before,
            },
        )
        persisted_frozen_hashes = set()
        for reservation in reservations:
            proposal_path = self._attempt_dir(reservation.attempt_id) / "proposal.json"
            if not proposal_path.exists():
                continue
            existing = self._load_proposal(
                proposal_path, reservation.execution_id, reservation.attempt_id
            )
            if existing.get("epoch") != epoch or existing.get("phase") != phase:
                raise ValueError("uncommitted parallel proposal epoch/phase mismatch")
            persisted_frozen_hashes.add(existing.get("frozen_state_hash"))
        if len(persisted_frozen_hashes) > 1 or None in persisted_frozen_hashes:
            raise ValueError("uncommitted parallel proposals mix frozen state hashes")
        frozen_state_hash = (
            next(iter(persisted_frozen_hashes))
            if persisted_frozen_hashes
            else frozen.state_hash
        )
        epoch_dir = self.out_dir / "epochs" / f"epoch-{epoch:04d}"
        generation_dir = epoch_dir / "generation"
        generation_dir.mkdir(parents=True, exist_ok=True)
        epoch_results = None
        epoch_generator = self._proposal_generator(phase)
        propose_epoch = getattr(epoch_generator, "propose_epoch", None)
        if callable(propose_epoch):
            try:
                epoch_results = list(
                    propose_epoch(
                        reservations,
                        batch_dir=generation_dir,
                        epoch=epoch,
                        tree_version=frozen.tree_version,
                        frozen_state_hash=frozen_state_hash,
                        resource_pool=self.resource_pool,
                    )
                )
                if len(epoch_results) != len(reservations):
                    raise ValueError(
                        "epoch generator returned the wrong proposal cardinality"
                    )
            except Exception as error:
                epoch_results = [
                    {
                        "candidate": None,
                        "source": (
                            "bootstrap" if phase == "bootstrap" else self.method
                        ),
                        "error": {
                            "type": type(error).__name__,
                            "reason": getattr(error, "reason", "generation-error"),
                            "message": str(error)[:500],
                        },
                    }
                    for _ in reservations
                ]
        entries = []
        for slot, reservation in enumerate(reservations):
            proposal_path = self._attempt_dir(reservation.attempt_id) / "proposal.json"
            if proposal_path.exists():
                proposal = self._load_proposal(
                    proposal_path, reservation.execution_id, reservation.attempt_id
                )
                self._restore_snapshots(
                    proposal["generator_state"],
                    proposal["bootstrap_generator_state"],
                )
            else:
                proposal = self._create_proposal(
                    reservation.execution_id,
                    reservation.attempt_id,
                    phase,
                    reservation=reservation,
                    epoch=epoch,
                    frozen_state_hash=frozen_state_hash,
                    proposal_result=(
                        epoch_results[slot]
                        if epoch_results is not None
                        else _UNSET_PROPOSAL
                    ),
                )
            entries.append(
                {
                    "execution_id": reservation.execution_id,
                    "attempt_id": reservation.attempt_id,
                    "candidate_id": (
                        proposal["candidate"].get("candidate_id")
                        if proposal.get("candidate") is not None
                        else None
                    ),
                }
            )
        main_after, bootstrap_after = self._snapshots()
        self.state["generator_state"] = main_after
        self.state["bootstrap_generator_state"] = bootstrap_after
        active = {
            "schema": "campaign-active-epoch/1",
            "epoch": epoch,
            "phase": phase,
            "frozen_state_hash": frozen_state_hash,
            "tree_version": frozen.tree_version,
            "entries": entries,
            "epoch_dir": str(epoch_dir),
        }
        self.state["active_epoch"] = active
        self.state["next_execution_ordinal"] = start + capacity
        self._save()
        return active

    def _parallel_worker_job(
        self, proposal: Mapping[str, Any], active: Mapping[str, Any]
    ) -> WorkerJob:
        candidate = proposal.get("candidate")
        if not isinstance(candidate, dict):
            raise ValueError("parallel worker requires a generated candidate")
        return WorkerJob(
            candidate=candidate,
            phase=proposal["phase"],
            epoch=active["epoch"],
            result_dir=(
                pathlib.Path(active["epoch_dir"])
                / "workers"
                / candidate["candidate_id"]
            ),
            frozen_state_hash=active["frozen_state_hash"],
        )

    def _call_parallel_executor(self, candidate, *, lifecycle):
        provenance = candidate.get("provenance") or {}
        execution_id = provenance.get("execution_id")
        attempt_id = provenance.get("attempt_id")
        if not isinstance(execution_id, str) or not isinstance(attempt_id, str):
            raise ValueError("parallel candidate lacks reserved execution coordinates")
        execute = getattr(self.executor, "execute", self.executor)
        try:
            signature = inspect.signature(execute)
        except (TypeError, ValueError):
            signature = None
        accepts_lifecycle = signature is not None and (
            "lifecycle" in signature.parameters
            or any(
                parameter.kind == parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
        )
        resource_context = (
            self.resource_pool.slots("api", "docker", "agent")
            if self.resource_pool is not None
            else contextlib.nullcontext()
        )
        def adapted_lifecycle(event, evidence):
            if event == "started":
                return lifecycle(event, evidence)
            if event == "external-terminal":
                return lifecycle(event, {"outcome": evidence.get("result")})
            raise ValueError(f"unsupported executor lifecycle event {event!r}")

        with resource_context:
            if accepts_lifecycle:
                result = execute(
                    candidate,
                    execution_id,
                    attempt_id,
                    lifecycle=adapted_lifecycle,
                )
            else:
                result = execute(candidate, execution_id, attempt_id)
        result = _json_copy(
            self._validate_result(result), label="parallel executor result"
        )
        result.update(
            {
                "execution_id": execution_id,
                "attempt_id": attempt_id,
                "epoch": provenance.get("epoch"),
            }
        )
        return result

    def _publish_parallel_receipt(self, proposal, worker_result):
        execution_id = proposal["execution_id"]
        attempt_id = proposal["attempt_id"]
        path = self._attempt_dir(attempt_id) / "receipt.json"
        if path.exists():
            return self._load_receipt(path, execution_id, attempt_id)
        result = _json_copy(
            worker_result.outcome_json(), label="parallel worker outcome"
        )
        result.setdefault("execution_id", execution_id)
        result.setdefault("attempt_id", attempt_id)
        result.setdefault("epoch", proposal.get("epoch"))
        result["worker_receipt_hash"] = worker_result.receipt_hash
        result["worker_job_hash"] = worker_result.job_hash
        receipt = {
            "schema": "campaign-attempt-receipt/1",
            **self._identity_envelope(execution_id, attempt_id),
            "candidate_id": proposal["candidate"]["candidate_id"],
            "result": result,
            "lifecycle_journal": {},
        }
        _publish_immutable(path, receipt, label="receipt")
        return receipt

    def _reduce_parallel_result(self, proposal, worker_result):
        receipt = self._publish_parallel_receipt(proposal, worker_result)
        cleanup, cleanup_hash, cleanup_intent_hash = self._ensure_cleanup(
            proposal, proposal["execution_id"], proposal["attempt_id"]
        )
        record = self._commit_attempt(
            proposal,
            receipt,
            cleanup,
            cleanup_hash,
            cleanup_intent_hash,
        )
        if record["consume_budget"]:
            self._finish_fold(
                proposal, receipt, restore_proposal_state=False
            )
        else:
            main, bootstrap = self._snapshots()
            self.state["generator_state"] = main
            self.state["bootstrap_generator_state"] = bootstrap
            self._save()
        return {
            "attempt_id": proposal["attempt_id"],
            "candidate_id": proposal.get("candidate", {}).get("candidate_id"),
            "consume_budget": record["consume_budget"],
            "fold_status": record.get("fold_status"),
        }

    def _run_active_parallel_epoch(self, active):
        assert self.state is not None
        proposals = {}
        generation_failures = []
        for entry in active["entries"]:
            proposal = self._load_proposal(
                self._attempt_dir(entry["attempt_id"]) / "proposal.json",
                entry["execution_id"],
                entry["attempt_id"],
            )
            if proposal.get("candidate") is None:
                generation_failures.append(proposal)
            else:
                proposals[proposal["candidate"]["candidate_id"]] = proposal

        for proposal in generation_failures:
            receipt = self._execute_and_publish(
                proposal, proposal["execution_id"], proposal["attempt_id"]
            )
            cleanup, cleanup_hash, intent_hash = self._ensure_cleanup(
                proposal, proposal["execution_id"], proposal["attempt_id"]
            )
            self._commit_attempt(
                proposal, receipt, cleanup, cleanup_hash, intent_hash
            )

        jobs = [self._parallel_worker_job(proposal, active) for proposal in proposals.values()]
        reducer = ParallelReducer(
            lambda result: self._reduce_parallel_result(
                proposals[result.candidate_id], result
            ),
            progress_dir=pathlib.Path(active["epoch_dir"]) / "fold-progress",
        )
        before = self.state["counted_executions"]
        run_epoch(
            jobs,
            executor=self._call_parallel_executor,
            max_workers=max(1, min(self.epoch_size, len(jobs) or 1)),
            reducer=reducer,
            plan_path=pathlib.Path(active["epoch_dir"]) / "plan.json",
        )
        gained = self.state["counted_executions"] - before
        failed = len(active["entries"]) - gained
        if gained:
            self.state["consecutive_parallel_pre_agent_failures"] = 0
        else:
            self.state["consecutive_parallel_pre_agent_failures"] = (
                int(self.state.get("consecutive_parallel_pre_agent_failures", 0))
                + failed
            )
        self.state["active_epoch"] = None
        self.state["next_epoch"] = int(active["epoch"]) + 1
        self._save()
        return gained

    def _run_parallel(self) -> dict[str, Any]:
        assert self.state is not None
        while self.state["counted_executions"] < self.protocol.budget:
            active = self.state.get("active_epoch") or self._plan_parallel_epoch()
            self._run_active_parallel_epoch(active)
            if (
                self.state.get("consecutive_parallel_pre_agent_failures", 0)
                >= self.protocol.max_generation_attempts_per_execution
            ):
                self.state["status"] = "aborted_pre_agent_attempt_cap"
                self.state["stop_reason"] = "generation-attempt-cap"
                self.state["complete"] = False
                self._finish_campaign()
                return self.state
        self.state["status"] = "completed"
        self.state["stop_reason"] = None
        self.state["complete"] = True
        self._finish_campaign()
        return self.state

    def _process_attempt(self, ordinal: int, attempt_number: int) -> bool:
        assert self.state is not None
        execution_id = f"e{ordinal:04d}"
        attempt_id = f"{execution_id}-a{attempt_number:02d}"
        directory = self._attempt_dir(attempt_id)
        proposal_path = directory / "proposal.json"
        receipt_path = directory / "receipt.json"

        if receipt_path.exists() and not proposal_path.exists():
            raise ValueError(f"malformed receipt {attempt_id}: proposal is missing")
        if proposal_path.exists():
            proposal = self._load_proposal(proposal_path, execution_id, attempt_id)
            fold_path = directory / "fold.json"
            if self.state.get("pending_fold") == attempt_id and fold_path.exists():
                fold = self._load_fold(fold_path, execution_id, attempt_id)
                self._restore_snapshots(
                    fold["generator_state"], fold["bootstrap_generator_state"]
                )
            else:
                self._restore_snapshots(
                    proposal["generator_state"],
                    proposal["bootstrap_generator_state"],
                    forward_attempt_id=(
                        attempt_id
                        if self.state.get("pending_fold") == attempt_id
                        else None
                    ),
                )
        else:
            proposal = self._create_proposal(
                execution_id, attempt_id, self._phase(ordinal)
            )
        if receipt_path.exists():
            receipt = self._load_receipt(receipt_path, execution_id, attempt_id)
        else:
            receipt = self._execute_and_publish(proposal, execution_id, attempt_id)
        cleanup, cleanup_hash, cleanup_intent_hash = self._ensure_cleanup(
            proposal, execution_id, attempt_id
        )
        record = self._commit_attempt(
            proposal,
            receipt,
            cleanup,
            cleanup_hash,
            cleanup_intent_hash,
        )
        if record["consume_budget"]:
            self._finish_fold(proposal, receipt)
            return True
        return False

    def _drain_unused_images(self) -> None:
        """Clean batch-built candidates that can no longer be used by this campaign."""
        assert self.state is not None
        changed = False
        for name, generator in (
            ("main", self.generator),
            ("bootstrap", self.bootstrap_generator),
        ):
            if generator is None or not hasattr(generator, "drain_buffer"):
                continue
            for candidate in generator.drain_buffer():
                candidate_id = candidate.get("candidate_id") or canonical_json_hash(candidate)[:12]
                cleanup_candidate_image(
                    candidate,
                    remover=self.image_remover,
                    receipt_path=self.out_dir / "unused-images" / f"{name}-{candidate_id}.json",
                )
                changed = True
        if changed:
            main, bootstrap = self._snapshots()
            self.state["generator_state"] = main
            self.state["bootstrap_generator_state"] = bootstrap

    def _finish_campaign(self) -> None:
        assert self.state is not None
        self._drain_unused_images()
        iterations = self.state["iterations"]
        self.state["totals"] = {
            "runs": len(iterations),
            "attempts": len(self.state["attempts"]),
            "distinct_violated_properties": sorted(
                {
                    prop
                    for record in iterations
                    for prop in record.get("violated", [])
                }
            ),
            "runs_with_violation": sum(
                bool(record.get("violated")) for record in iterations
            ),
        }
        self._save()

    def run(self) -> dict[str, Any]:
        state = self._load_or_create()
        if state["complete"]:
            return state
        if state.get("stop_reason") in {
            "generation-attempt-cap",
            "external-outcome-unknown",
        }:
            return state
        if self.epoch_size > 1:
            return self._run_parallel()

        while state["counted_executions"] < self.protocol.budget:
            ordinal = state["counted_executions"]
            consumed = False
            for attempt_number in range(
                self.protocol.max_generation_attempts_per_execution
            ):
                attempt_id = f"e{ordinal:04d}-a{attempt_number:02d}"
                committed = self._committed_attempt(attempt_id)
                if committed is not None and not committed["consume_budget"]:
                    continue
                consumed = self._process_attempt(ordinal, attempt_number)
                committed = self._committed_attempt(attempt_id)
                if (
                    committed is not None
                    and committed["result"].get("stop_campaign") is True
                ):
                    state["status"] = "aborted_external_outcome_unknown"
                    state["stop_reason"] = "external-outcome-unknown"
                    state["complete"] = False
                    self._finish_campaign()
                    return state
                if consumed:
                    break
            if not consumed:
                state["status"] = "aborted_pre_agent_attempt_cap"
                state["stop_reason"] = "generation-attempt-cap"
                state["complete"] = False
                state["consecutive_pre_agent_failures"] = (
                    self.protocol.max_generation_attempts_per_execution
                )
                self._finish_campaign()
                return state

        state["status"] = "completed"
        state["stop_reason"] = None
        state["complete"] = True
        self._finish_campaign()
        return state
