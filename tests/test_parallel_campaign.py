from __future__ import annotations

import json
import time

import pytest

from skillrace.parallel_campaign import (
    ParallelReducer,
    WorkerJob,
    run_epoch,
    run_worker,
)


def _job(tmp_path, candidate_id, *, frozen_state_hash=None):
    return WorkerJob(
        candidate={
            "candidate_id": candidate_id,
            "nested": {"values": [1, 2]},
        },
        phase="explore",
        epoch=2,
        result_dir=tmp_path / "workers" / candidate_id,
        frozen_state_hash=frozen_state_hash,
    )


def test_worker_job_is_deeply_immutable_and_writes_only_its_result_directory(tmp_path):
    shared = tmp_path / "campaign.json"
    shared.write_text('{"sentinel":true}\n')
    job = _job(tmp_path, "cand-a")

    with pytest.raises(TypeError):
        job.candidate["nested"]["values"][0] = 99

    result = run_worker(
        job,
        executor=lambda candidate: {
            "agent_started": True,
            "status": "completed",
            "run_dir": "runs/a",
        },
    )

    assert json.loads(shared.read_text()) == {"sentinel": True}
    assert (job.result_dir / "receipt.json").is_file()
    assert result.candidate_id == "cand-a"
    assert result.outcome["status"] == "completed"


def test_worker_receipt_binds_frozen_adaptive_state_hash(tmp_path):
    state_hash = "d" * 64
    job = _job(tmp_path, "cand-frozen", frozen_state_hash=state_hash)
    result = run_worker(
        job,
        executor=lambda candidate: {"agent_started": True, "status": "completed"},
    )
    receipt = json.loads((job.result_dir / "receipt.json").read_text())

    assert result.frozen_state_hash == state_hash
    assert receipt["frozen_state_hash"] == state_hash
    assert job.job_hash != _job(
        tmp_path / "other", "cand-frozen", frozen_state_hash="e" * 64
    ).job_hash


def test_worker_receipt_is_replayed_without_execution_and_tampering_is_rejected(tmp_path):
    job = _job(tmp_path, "cand-a")
    calls = []
    first = run_worker(
        job,
        executor=lambda candidate: calls.append(candidate["candidate_id"])
        or {"agent_started": True, "status": "completed"},
    )
    second = run_worker(
        job,
        executor=lambda candidate: (_ for _ in ()).throw(
            AssertionError("immutable receipt was re-executed")
        ),
    )
    assert first == second
    assert calls == ["cand-a"]

    receipt = job.result_dir / "receipt.json"
    changed = json.loads(receipt.read_text())
    changed["outcome"]["status"] = "changed"
    receipt.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="receipt hash"):
        run_worker(job, executor=lambda candidate: {})


def test_worker_exception_becomes_an_immutable_infrastructure_receipt(tmp_path):
    job = _job(tmp_path, "cand-error")
    calls = []

    def fail(candidate):
        calls.append(candidate["candidate_id"])
        raise RuntimeError("adapter unavailable")

    first = run_worker(job, executor=fail)
    second = run_worker(
        job,
        executor=lambda candidate: (_ for _ in ()).throw(
            AssertionError("infrastructure receipt was re-executed")
        ),
    )

    assert first == second
    assert calls == ["cand-error"]
    assert first.outcome["agent_started"] is False
    assert first.outcome["status"] == "worker-infrastructure-error"
    assert first.outcome["infrastructure_status"] == "executor_error"
    assert "adapter unavailable" in first.outcome["error"]


def test_worker_started_without_terminal_is_conservatively_recovered_without_rerun(
    tmp_path,
):
    job = _job(tmp_path, "cand-started")
    calls = []

    def crash(candidate, *, lifecycle):
        calls.append(candidate["candidate_id"])
        lifecycle("started", {"run_dir": "runs/cand-started"})
        raise SystemExit("worker died")

    with pytest.raises(SystemExit):
        run_worker(job, executor=crash)

    recovered = run_worker(
        job,
        executor=lambda candidate: (_ for _ in ()).throw(
            AssertionError("post-start worker was rerun")
        ),
    )
    assert calls == ["cand-started"]
    assert recovered.outcome["agent_started"] is None
    assert recovered.outcome["consume_budget_conservatively"] is True
    assert recovered.outcome["cost_accounting"] == "unknown-nonzero-possible"


def test_empty_unreceipted_worker_directory_is_safe_to_retry(tmp_path):
    job = _job(tmp_path, "cand-empty")
    job.result_dir.mkdir(parents=True)

    result = run_worker(
        job,
        executor=lambda candidate: {"agent_started": False, "status": "rejected"},
    )
    assert result.outcome["status"] == "rejected"


def test_reducer_rejects_duplicate_candidates_and_folds_in_candidate_order(tmp_path):
    results = [
        run_worker(
            _job(tmp_path, candidate),
            executor=lambda value: {"agent_started": True, "status": "completed"},
        )
        for candidate in ("cand-z", "cand-a", "cand-m")
    ]
    folded = []
    reducer = ParallelReducer(fold=lambda result: folded.append(result.candidate_id))

    reducer.reduce(results)
    assert folded == ["cand-a", "cand-m", "cand-z"]

    with pytest.raises(ValueError, match="duplicate"):
        reducer.reduce([results[0], results[0]])


def test_reducer_persists_per_result_fold_progress_and_replays_without_refold(tmp_path):
    results = [
        run_worker(
            _job(tmp_path, candidate),
            executor=lambda value: {"agent_started": True, "status": "completed"},
        )
        for candidate in ("cand-b", "cand-a")
    ]
    calls = []
    progress = tmp_path / "fold-progress"
    first = ParallelReducer(
        fold=lambda result: calls.append(result.candidate_id)
        or {"folded": result.candidate_id},
        progress_dir=progress,
    )
    output = first.reduce(results)
    replay = ParallelReducer(
        fold=lambda result: (_ for _ in ()).throw(AssertionError("result refolded")),
        progress_dir=progress,
    )

    assert replay.reduce(list(reversed(results))) == output
    assert calls == ["cand-a", "cand-b"]
    assert sorted(path.name for path in progress.glob("*.json")) == [
        "cand-a.json", "cand-b.json"
    ]


def test_completion_order_does_not_change_reduction_order(tmp_path):
    def one_run(root, delays):
        jobs = [_job(root, candidate) for candidate in ("cand-c", "cand-a", "cand-b")]
        completed = []
        folded = []

        def execute(candidate):
            time.sleep(delays[candidate["candidate_id"]])
            return {"agent_started": True, "status": "completed"}

        results = run_epoch(
            jobs,
            executor=execute,
            max_workers=3,
            reducer=ParallelReducer(lambda result: folded.append(result.candidate_id)),
            completion_observer=lambda result: completed.append(result.candidate_id),
            plan_path=root / "epoch-plan.json",
        )
        assert (root / "epoch-plan.json").is_file()
        return completed, folded, [result.candidate_id for result in results]

    first = one_run(
        tmp_path / "first",
        {"cand-a": 0.03, "cand-b": 0.02, "cand-c": 0.0},
    )
    second = one_run(
        tmp_path / "second",
        {"cand-a": 0.0, "cand-b": 0.02, "cand-c": 0.03},
    )

    assert first[0] != second[0]
    assert first[1:] == second[1:] == (
        ["cand-a", "cand-b", "cand-c"],
        ["cand-a", "cand-b", "cand-c"],
    )
