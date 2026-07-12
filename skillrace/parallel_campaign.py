"""Deterministic primitives for bounded parallel campaign epochs.

Workers receive deeply immutable jobs and may publish only a receipt inside their
own result directory.  Adaptive state remains reducer-owned and is folded only
after an epoch has fully completed, in stable candidate-ID order.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import pathlib
import re
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .io_utils import atomic_write_json, canonical_json_hash


_CANDIDATE_ID = re.compile(r"cand-[0-9a-f]{16}\Z")


def candidate_id(
    campaign_id: str,
    execution_id: str,
    attempt_id: str,
    slot: int,
) -> str:
    """Return one stable, campaign-scoped proposal identity."""
    for label, value in (
        ("campaign_id", campaign_id),
        ("execution_id", execution_id),
        ("attempt_id", attempt_id),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} must be a nonempty string")
    if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
        raise ValueError("candidate slot must be a non-negative integer")
    payload = f"{campaign_id}\0{execution_id}\0{attempt_id}\0{slot}".encode("utf-8")
    return "cand-" + hashlib.sha256(payload).hexdigest()[:16]


@dataclass(frozen=True)
class CandidateReservation:
    campaign_id: str
    execution_id: str
    attempt_id: str
    slot: int
    epoch: int
    candidate_id: str = field(init=False)
    provenance: Mapping[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.epoch, int) or isinstance(self.epoch, bool) or self.epoch < 0:
            raise ValueError("reservation epoch must be a non-negative integer")
        identity = candidate_id(
            self.campaign_id, self.execution_id, self.attempt_id, self.slot
        )
        object.__setattr__(self, "candidate_id", identity)
        object.__setattr__(
            self,
            "provenance",
            MappingProxyType(
                {
                    "campaign_id": self.campaign_id,
                    "execution_id": self.execution_id,
                    "attempt_id": self.attempt_id,
                    "epoch": self.epoch,
                    "slot": self.slot,
                }
            ),
        )


def make_reservations(
    campaign_id: str,
    coordinates: Iterable[tuple[str, str]],
    *,
    epoch: int,
) -> tuple[CandidateReservation, ...]:
    coordinates = list(coordinates)
    if len(coordinates) != len(set(coordinates)):
        raise ValueError("duplicate execution/attempt reservation coordinates")
    return tuple(
        CandidateReservation(
            campaign_id=campaign_id,
            execution_id=execution_id,
            attempt_id=attempt_id,
            slot=slot,
            epoch=epoch,
        )
        for slot, (execution_id, attempt_id) in enumerate(coordinates)
    )


def _json_copy(value: Any, *, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be JSON serializable: {error}") from error


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _read_transition(path: pathlib.Path, *, schema: str) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"malformed durable state transition at {path}: {error}") from error
    if not isinstance(record, dict) or record.get("schema") != schema:
        raise ValueError(f"malformed durable state transition schema at {path}")
    core = {key: value for key, value in record.items() if key != "transition_hash"}
    if record.get("transition_hash") != canonical_json_hash(core):
        raise ValueError(f"durable state transition hash mismatch at {path}")
    if (
        record.get("pre_state_hash") != canonical_json_hash(record.get("pre_state"))
        or record.get("post_state_hash") != canonical_json_hash(record.get("post_state"))
    ):
        raise ValueError(f"durable state transition snapshot hash mismatch at {path}")
    return record


def load_state_transition(
    path: str | pathlib.Path,
    *,
    schema: str,
    request_hash: str,
) -> dict[str, Any] | None:
    path = pathlib.Path(path)
    if not path.exists():
        return None
    record = _read_transition(path, schema=schema)
    if record.get("request_hash") != request_hash:
        raise ValueError(f"durable state transition request mismatch at {path}")
    return record


def read_state_transition(
    path: str | pathlib.Path, *, schema: str
) -> dict[str, Any]:
    return _read_transition(pathlib.Path(path), schema=schema)


def publish_state_transition(
    path: str | pathlib.Path,
    *,
    schema: str,
    request_hash: str,
    pre_state: Mapping[str, Any],
    post_state: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    path = pathlib.Path(path)
    pre = _json_copy(pre_state, label="transition pre-state")
    post = _json_copy(post_state, label="transition post-state")
    body = _json_copy(payload, label="transition payload")
    core = {
        "schema": schema,
        "request_hash": request_hash,
        "pre_state_hash": canonical_json_hash(pre),
        "post_state_hash": canonical_json_hash(post),
        "pre_state": pre,
        "post_state": post,
        "payload": body,
    }
    record = {**core, "transition_hash": canonical_json_hash(core)}
    existing = load_state_transition(
        path, schema=schema, request_hash=request_hash
    )
    if existing is not None:
        if canonical_json_hash(existing) != canonical_json_hash(record):
            raise ValueError(f"conflicting durable state transition at {path}")
        return existing
    atomic_write_json(path, record)
    return record


def apply_state_transition(current_state, transition, *, restore) -> None:
    current_hash = canonical_json_hash(current_state)
    if current_hash == transition["post_state_hash"]:
        return
    if current_hash != transition["pre_state_hash"]:
        raise ValueError("generator state matches neither side of durable transition")
    restore(_json_copy(transition["post_state"], label="transition post-state"))


@dataclass(frozen=True)
class WorkerJob:
    candidate: Mapping[str, Any]
    phase: str
    epoch: int
    result_dir: pathlib.Path
    frozen_state_hash: str | None = None
    job_hash: str = field(init=False)

    def __post_init__(self) -> None:
        candidate = _json_copy(self.candidate, label="worker candidate")
        candidate_value = candidate.get("candidate_id")
        if not isinstance(candidate_value, str) or not candidate_value:
            raise ValueError("worker candidate requires a nonempty candidate_id")
        if not isinstance(self.phase, str) or not self.phase:
            raise ValueError("worker phase must be a nonempty string")
        if not isinstance(self.epoch, int) or isinstance(self.epoch, bool) or self.epoch < 0:
            raise ValueError("worker epoch must be a non-negative integer")
        if self.frozen_state_hash is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.frozen_state_hash
        ):
            raise ValueError("worker frozen_state_hash must be a SHA-256 digest")
        result_dir = pathlib.Path(self.result_dir)
        payload = {
            "candidate": candidate,
            "phase": self.phase,
            "epoch": self.epoch,
            "frozen_state_hash": self.frozen_state_hash,
        }
        object.__setattr__(self, "candidate", _freeze(candidate))
        object.__setattr__(self, "result_dir", result_dir)
        object.__setattr__(self, "job_hash", canonical_json_hash(payload))

    @property
    def candidate_id(self) -> str:
        return self.candidate["candidate_id"]


@dataclass(frozen=True)
class WorkerResult:
    candidate_id: str
    phase: str
    epoch: int
    outcome: Mapping[str, Any]
    result_dir: pathlib.Path
    job_hash: str
    receipt_hash: str
    frozen_state_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "outcome",
            _freeze(_json_copy(self.outcome, label="worker outcome")),
        )
        object.__setattr__(self, "result_dir", pathlib.Path(self.result_dir))

    def outcome_json(self) -> dict[str, Any]:
        return _thaw(self.outcome)


def _validate_outcome(value: Any) -> dict[str, Any]:
    value = _json_copy(value, label="worker outcome")
    if not isinstance(value, dict):
        raise ValueError("worker outcome must be an object")
    if value.get("agent_started") is not None and not isinstance(
        value.get("agent_started"), bool
    ):
        raise ValueError("worker outcome agent_started must be boolean or null")
    if not isinstance(value.get("status"), str) or not value["status"]:
        raise ValueError("worker outcome status must be a nonempty string")
    return value


_WORKER_LIFECYCLE = {
    "started": "worker-external-started/1",
    "external-terminal": "worker-external-terminal/1",
    "executor-terminal": "worker-executor-terminal/1",
}


def _worker_lifecycle_path(job: WorkerJob, event: str) -> pathlib.Path:
    return job.result_dir / f"{event}.json"


def _publish_worker_lifecycle(job: WorkerJob, event: str, evidence) -> dict[str, Any]:
    if event not in _WORKER_LIFECYCLE or not isinstance(evidence, Mapping):
        raise ValueError("malformed worker lifecycle event")
    core = {
        "schema": _WORKER_LIFECYCLE[event],
        "job_hash": job.job_hash,
        "candidate_id": job.candidate_id,
        "frozen_state_hash": job.frozen_state_hash,
    }
    if event == "started":
        core["evidence"] = _json_copy(evidence, label="worker started evidence")
    else:
        core["outcome"] = _validate_outcome(evidence.get("outcome"))
    record = {**core, "lifecycle_hash": canonical_json_hash(core)}
    path = _worker_lifecycle_path(job, event)
    if path.exists():
        existing = _load_worker_lifecycle(job, event)
        if canonical_json_hash(existing) != canonical_json_hash(record):
            raise ValueError("conflicting immutable worker lifecycle record")
        return existing
    atomic_write_json(path, record)
    return record


def _load_worker_lifecycle(job: WorkerJob, event: str) -> dict[str, Any] | None:
    path = _worker_lifecycle_path(job, event)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"malformed worker lifecycle record: {error}") from error
    if not isinstance(record, dict):
        raise ValueError("malformed worker lifecycle record")
    core = {key: value for key, value in record.items() if key != "lifecycle_hash"}
    if (
        record.get("schema") != _WORKER_LIFECYCLE[event]
        or record.get("job_hash") != job.job_hash
        or record.get("candidate_id") != job.candidate_id
        or record.get("frozen_state_hash") != job.frozen_state_hash
        or record.get("lifecycle_hash") != canonical_json_hash(core)
    ):
        raise ValueError("worker lifecycle record identity/hash mismatch")
    if event == "started":
        if not isinstance(record.get("evidence"), dict):
            raise ValueError("malformed worker started evidence")
    else:
        _validate_outcome(record.get("outcome"))
    return record


def _worker_lifecycle_hashes(job: WorkerJob) -> dict[str, str]:
    hashes = {}
    for event in _WORKER_LIFECYCLE:
        record = _load_worker_lifecycle(job, event)
        if record is not None:
            hashes[event] = record["lifecycle_hash"]
    return hashes


def _recover_worker_outcome(job: WorkerJob) -> dict[str, Any] | None:
    for event in ("executor-terminal", "external-terminal"):
        record = _load_worker_lifecycle(job, event)
        if record is not None:
            outcome = _json_copy(record["outcome"], label="recovered worker outcome")
            outcome["lifecycle_recovery"] = event
            if event == "external-terminal":
                outcome["cost_accounting"] = "unknown-nonzero-possible"
                outcome["unrecorded_cost_possible"] = True
            return outcome
    started = _load_worker_lifecycle(job, "started")
    if started is None:
        return None
    return {
        "agent_started": None,
        "launch_committed": None,
        "status": "worker-external-outcome-indeterminate",
        "infrastructure_status": "external_state_indeterminate",
        "oracle_status": "not_run",
        "violated": [],
        "inconclusive": [],
        "consume_budget_conservatively": True,
        "budget_accounting_reason": "launch-state-indeterminate",
        "cost_accounting": "unknown-nonzero-possible",
        "unrecorded_cost_possible": True,
        "lifecycle_recovery": "started-only",
    }


def _result_from_receipt(job: WorkerJob, receipt: Mapping[str, Any]) -> WorkerResult:
    if receipt.get("schema") != "parallel-worker-receipt/1":
        raise ValueError("malformed worker receipt schema")
    core = {key: value for key, value in receipt.items() if key != "receipt_hash"}
    expected_hash = canonical_json_hash(core)
    if receipt.get("receipt_hash") != expected_hash:
        raise ValueError("worker receipt hash mismatch")
    if receipt.get("job_hash") != job.job_hash:
        raise ValueError("worker receipt belongs to a different immutable job")
    if (
        receipt.get("candidate_id") != job.candidate_id
        or receipt.get("phase") != job.phase
        or receipt.get("epoch") != job.epoch
        or receipt.get("frozen_state_hash") != job.frozen_state_hash
    ):
        raise ValueError("worker receipt identity does not match its job")
    outcome = _validate_outcome(receipt.get("outcome"))
    if receipt.get("lifecycle_journal", {}) != _worker_lifecycle_hashes(job):
        raise ValueError("worker receipt lifecycle journal mismatch")
    return WorkerResult(
        candidate_id=job.candidate_id,
        phase=job.phase,
        epoch=job.epoch,
        outcome=outcome,
        result_dir=job.result_dir,
        job_hash=job.job_hash,
        receipt_hash=expected_hash,
        frozen_state_hash=job.frozen_state_hash,
    )


def run_worker(
    job: WorkerJob,
    *,
    executor: Callable[[dict[str, Any]], Mapping[str, Any]],
) -> WorkerResult:
    """Execute or replay exactly one isolated worker receipt."""
    receipt_path = job.result_dir / "receipt.json"
    if receipt_path.exists():
        try:
            receipt = json.loads(receipt_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"malformed worker receipt: {error}") from error
        if not isinstance(receipt, dict):
            raise ValueError("malformed worker receipt: expected an object")
        return _result_from_receipt(job, receipt)
    job.result_dir.parent.mkdir(parents=True, exist_ok=True)
    if job.result_dir.exists():
        known = {f"{event}.json" for event in _WORKER_LIFECYCLE}
        unknown = {path.name for path in job.result_dir.iterdir()} - known
        if unknown:
            raise ValueError(
                f"worker result directory has unknown unreceipted artifacts: {sorted(unknown)}"
            )
    else:
        job.result_dir.mkdir()

    raw_outcome = _recover_worker_outcome(job)
    if raw_outcome is None:
        callback_failure: list[BaseException] = []

        def lifecycle(event, evidence):
            try:
                return _publish_worker_lifecycle(job, event, evidence)
            except BaseException as error:
                callback_failure.append(error)
                raise

        try:
            signature = inspect.signature(executor)
            accepts_lifecycle = "lifecycle" in signature.parameters or any(
                parameter.kind == parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            if accepts_lifecycle:
                raw_outcome = executor(_thaw(job.candidate), lifecycle=lifecycle)
            else:
                raw_outcome = executor(_thaw(job.candidate))
        except Exception as error:
            if callback_failure and callback_failure[-1] is error:
                raise
            raw_outcome = _recover_worker_outcome(job)
            if raw_outcome is None:
                raw_outcome = {
                    "agent_started": False,
                    "status": "worker-infrastructure-error",
                    "infrastructure_status": "executor_error",
                    "oracle_status": "not_run",
                    "violated": [],
                    "inconclusive": [],
                    "error": str(error)[:500],
                }
        raw_outcome = _validate_outcome(raw_outcome)
        _publish_worker_lifecycle(
            job, "executor-terminal", {"outcome": raw_outcome}
        )
    outcome = _validate_outcome(raw_outcome)
    core = {
        "schema": "parallel-worker-receipt/1",
        "job_hash": job.job_hash,
        "candidate_id": job.candidate_id,
        "phase": job.phase,
        "epoch": job.epoch,
        "frozen_state_hash": job.frozen_state_hash,
        "outcome": outcome,
        "lifecycle_journal": _worker_lifecycle_hashes(job),
    }
    receipt = {**core, "receipt_hash": canonical_json_hash(core)}
    atomic_write_json(receipt_path, receipt)
    return _result_from_receipt(job, receipt)


class ParallelReducer:
    """The sole serial owner of campaign/adaptive fold state."""

    def __init__(
        self,
        fold: Callable[[WorkerResult], Any],
        *,
        progress_dir: str | pathlib.Path | None = None,
    ):
        self._fold = fold
        self._progress_dir = (
            pathlib.Path(progress_dir) if progress_dir is not None else None
        )

    def reduce(self, results: Iterable[WorkerResult]) -> list[Any]:
        ordered = sorted(results, key=lambda result: result.candidate_id)
        identities = [result.candidate_id for result in ordered]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate candidate result in one epoch")
        folded = []
        for result in ordered:
            if self._progress_dir is None:
                folded.append(self._fold(result))
                continue
            path = self._progress_dir / f"{result.candidate_id}.json"
            if path.exists():
                try:
                    receipt = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError) as error:
                    raise ValueError(f"malformed fold progress receipt: {error}") from error
                core = {
                    key: value for key, value in receipt.items() if key != "progress_hash"
                }
                if (
                    receipt.get("schema") != "parallel-fold-progress/1"
                    or receipt.get("candidate_id") != result.candidate_id
                    or receipt.get("worker_receipt_hash") != result.receipt_hash
                    or receipt.get("job_hash") != result.job_hash
                    or receipt.get("progress_hash") != canonical_json_hash(core)
                ):
                    raise ValueError("fold progress receipt identity/hash mismatch")
                folded.append(receipt.get("fold_result"))
                continue
            value = _json_copy(self._fold(result), label="parallel fold result")
            core = {
                "schema": "parallel-fold-progress/1",
                "candidate_id": result.candidate_id,
                "worker_receipt_hash": result.receipt_hash,
                "job_hash": result.job_hash,
                "fold_result": value,
            }
            atomic_write_json(path, {**core, "progress_hash": canonical_json_hash(core)})
            folded.append(value)
        return folded


def persist_epoch_plan(path: str | pathlib.Path, jobs: Iterable[WorkerJob]):
    path = pathlib.Path(path)
    jobs = list(jobs)
    body = {
        "schema": "parallel-epoch-plan/1",
        "epoch": jobs[0].epoch if jobs else None,
        "jobs": [
            {
                "candidate_id": job.candidate_id,
                "job_hash": job.job_hash,
                "phase": job.phase,
                "epoch": job.epoch,
                "result_dir": str(job.result_dir),
                "frozen_state_hash": job.frozen_state_hash,
            }
            for job in jobs
        ],
    }
    if len({job.epoch for job in jobs}) > 1:
        raise ValueError("one epoch plan cannot mix epoch numbers")
    record = {**body, "plan_hash": canonical_json_hash(body)}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"malformed epoch plan at {path}: {error}") from error
        if canonical_json_hash(existing) != canonical_json_hash(record):
            raise ValueError(f"conflicting immutable epoch plan at {path}")
        return existing
    atomic_write_json(path, record)
    return record


def run_epoch(
    jobs: Iterable[WorkerJob],
    *,
    executor: Callable[[dict[str, Any]], Mapping[str, Any]],
    max_workers: int,
    reducer: ParallelReducer,
    plan_path: str | pathlib.Path,
    completion_observer: Callable[[WorkerResult], Any] | None = None,
) -> list[WorkerResult]:
    """Run a complete epoch concurrently, then reduce its results deterministically."""
    jobs = list(jobs)
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers <= 0:
        raise ValueError("max_workers must be a positive integer")
    if len({job.candidate_id for job in jobs}) != len(jobs):
        raise ValueError("duplicate candidate job in one epoch")
    persist_epoch_plan(plan_path, jobs)
    completed: list[WorkerResult] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(jobs)))) as pool:
        futures = {pool.submit(run_worker, job, executor=executor): job for job in jobs}
        try:
            for future in as_completed(futures):
                result = future.result()
                completed.append(result)
                if completion_observer is not None:
                    completion_observer(result)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    ordered = sorted(completed, key=lambda result: result.candidate_id)
    reducer.reduce(ordered)
    return ordered


@dataclass(frozen=True)
class FrozenAdaptiveState:
    tree_version: int
    artifacts: Mapping[str, Any]
    state_hash: str


def freeze_adaptive_state(
    *,
    tree_version: int,
    artifacts: Mapping[str, Any],
) -> FrozenAdaptiveState:
    if (
        not isinstance(tree_version, int)
        or isinstance(tree_version, bool)
        or tree_version < 0
    ):
        raise ValueError("tree version must be a non-negative integer")
    copied = _json_copy(artifacts, label="adaptive artifacts")
    if not isinstance(copied, dict):
        raise ValueError("adaptive artifacts must be an object")
    payload = {"tree_version": tree_version, "artifacts": copied}
    return FrozenAdaptiveState(
        tree_version=tree_version,
        artifacts=_freeze(copied),
        state_hash=canonical_json_hash(payload),
    )


def plan_epoch(
    method: str,
    targets: Iterable[Mapping[str, Any]],
    *,
    epoch: int,
    tree_version: int | None,
    limit: int,
    remaining_budget: int | None = None,
    agent_slots: int | None = None,
    frozen_state_hash: str | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Select one bounded batch without observing any result from that batch."""
    if method not in {"random", "greybox", "skillrace"}:
        raise ValueError(f"unsupported epoch method {method!r}")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise ValueError("epoch must be a non-negative integer")
    bounds = {"limit": limit}
    if remaining_budget is not None:
        bounds["remaining_budget"] = remaining_budget
    if agent_slots is not None:
        bounds["agent_slots"] = agent_slots
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in bounds.values()
    ):
        raise ValueError("epoch bounds must be non-negative integers")
    if method == "skillrace" and (
        not isinstance(tree_version, int)
        or isinstance(tree_version, bool)
        or tree_version < 0
    ):
        raise ValueError("SkillRACE epoch requires a non-negative tree version")
    if method == "skillrace" and (
        not isinstance(frozen_state_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", frozen_state_hash) is None
    ):
        raise ValueError("SkillRACE epoch requires a frozen state hash")

    copied_targets = []
    seen_targets = set()
    for raw in targets:
        target = _json_copy(raw, label="epoch target")
        if not isinstance(target, dict):
            raise ValueError("epoch target must be an object")
        if method == "skillrace":
            kind = target.get("kind", "target")
            if kind == "target":
                key = (target.get("branch_key"), target.get("mutation"))
                if not all(isinstance(value, str) and value for value in key):
                    raise ValueError(
                        "SkillRACE targets require branch_key and mutation"
                    )
            elif kind == "fallback":
                fallback_slot = target.get("fallback_slot")
                if (
                    not isinstance(fallback_slot, int)
                    or isinstance(fallback_slot, bool)
                    or fallback_slot < 0
                    or target.get("branch_key") is not None
                    or target.get("mutation") is not None
                ):
                    raise ValueError("malformed SkillRACE fallback target")
                key = ("fallback", fallback_slot)
            else:
                raise ValueError("unknown SkillRACE target kind")
            if key in seen_targets:
                continue
            seen_targets.add(key)
            supplied_hash = target.get("frozen_state_hash")
            if supplied_hash is not None and supplied_hash != frozen_state_hash:
                raise ValueError("SkillRACE target frozen state hash mismatch")
            target["frozen_state_hash"] = frozen_state_hash
        target["epoch"] = epoch
        target["tree_version"] = tree_version
        copied_targets.append(target)

    capacity = min(bounds.values()) if bounds else 0
    selected = copied_targets[:capacity]
    return tuple(_freeze(target) for target in selected)
