from __future__ import annotations

import json

from skillrace.io_utils import canonical_json_hash
from skillrace.patch_only import patch_campaign_failures, patch_failed_execution
from skillrace.repair_validation import FailureRepairRequest
from skillrace.revise_skill import package_hash


def _request(tmp_path, *, method="skillrace"):
    original = tmp_path / "original"
    original.mkdir(exist_ok=True)
    (original / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    (original / "helper.txt").write_text("unchanged\n", encoding="utf-8")
    case = tmp_path / "case"
    run = tmp_path / "run"
    case.mkdir(exist_ok=True)
    run.mkdir(exist_ok=True)
    return FailureRepairRequest(
        method=method,
        skill_name="demo",
        execution_id="e0001",
        attempt_id="e0001-a00",
        candidate_id="candidate-a",
        case_dir=case,
        original_skill_dir=original,
        original_skill_hash=package_hash(original),
        failed_property_ids=("behavior",),
        failure_signatures=("b" * 64,),
        run_dir=run,
        output_dir=tmp_path / "patches" / "patch-one",
        repair_id="patch-one",
    )


def _evidence(request):
    payload = {
        "schema": "skillrace-failure-repair-evidence/1",
        "original_skill_hash": request.original_skill_hash,
        "failure_core": {"task": "make the correct artifact"},
        "method_evidence": {"reasoning_episodes": [{"reasoning": "wrong branch"}]},
    }
    return {
        "schema": "skillrace-failure-repair-evidence/1",
        "repair_id": request.repair_id,
        "reviser_payload": payload,
        "evidence_hash": canonical_json_hash(payload),
    }


def _backend(calls, *, mutate_other=False, status="completed"):
    def backend(request, evidence, work_dir):
        calls.append(request.repair_id)
        if status != "completed":
            return {
                "status": status, "backend": "pi", "model": "test-model",
                "error_type": "bounded_stop", "error_message": "stopped before mutation",
            }
        skill = work_dir / "skill"
        skill.mkdir()
        for source in request.original_skill_dir.iterdir():
            if source.name == "properties.json":
                continue
            (skill / source.name).write_bytes(source.read_bytes())
        (skill / "SKILL.md").write_text("# Fixed\n", encoding="utf-8")
        if mutate_other:
            (skill / "helper.txt").write_text("changed\n", encoding="utf-8")
        return {
            "status": "completed",
            "skill_dir": str(skill),
            "backend": "pi",
            "model": "test-model",
            "operation_id": "patch.operation",
            "input_tokens": 11,
            "output_tokens": 3,
            "cache_read_tokens": 17,
            "turns": 4,
            "pi_tool_call_count": 3,
            "pi_mutation_count": 1,
            "pi_required_reads_remaining": 0,
            "pi_blocked_call_count": 2,
            "cost_provider_credits": 0.02,
            "wall_seconds": 1.5,
            "timeout_seconds": 120,
        }

    backend.backend_name = "pi"
    backend.model = "test-model"
    backend.timeout_seconds = 120
    return backend


def test_patch_only_completes_once_without_replay_artifacts(tmp_path):
    request = _request(tmp_path)
    calls = []
    result = patch_failed_execution(request, _evidence(request), backend=_backend(calls))

    assert result["status"] == "completed"
    assert result["backend"] == "pi"
    assert result["cache_read_tokens"] == 17
    assert result["turns"] == 4
    assert result["pi_tool_call_count"] == 3
    assert result["pi_mutation_count"] == 1
    assert result["pi_required_reads_remaining"] == 0
    assert result["pi_blocked_call_count"] == 2
    assert result["patched_skill_hash"] == package_hash(request.output_dir / "skill")
    assert calls == ["patch-one"]
    assert (request.output_dir / "skill.diff").read_text()
    assert not (request.output_dir / "replay").exists()
    assert not (request.output_dir / "result.json").exists()
    assert not (request.output_dir / "raw-response.txt").exists()
    assert not list(request.output_dir.rglob("session.jsonl"))

    resumed = patch_failed_execution(
        request,
        _evidence(request),
        backend=lambda *_: (_ for _ in ()).throw(AssertionError("must not repeat")),
    )
    assert resumed == result
    assert calls == ["patch-one"]


def test_patch_only_rejects_non_skill_change(tmp_path):
    request = _request(tmp_path)
    result = patch_failed_execution(
        request, _evidence(request), backend=_backend([], mutate_other=True)
    )
    assert result["status"] == "invalid_patch"
    assert not (request.output_dir / "skill").exists()


def test_campaign_only_inputs_are_not_required_in_patched_skill_package(tmp_path):
    request = _request(tmp_path)
    (request.original_skill_dir / "properties.json").write_text("[]\n")
    # Refresh the request identity after adding campaign-only evaluator metadata.
    request = FailureRepairRequest(
        **{**request.__dict__, "original_skill_hash": package_hash(request.original_skill_dir)}
    )
    result = patch_failed_execution(request, _evidence(request), backend=_backend([]))
    assert result["status"] == "completed"
    assert not (request.output_dir / "skill" / "properties.json").exists()


def test_patch_timeout_is_terminal_and_not_retried(tmp_path):
    request = _request(tmp_path)
    calls = []
    first = patch_failed_execution(
        request, _evidence(request), backend=_backend(calls, status="timeout")
    )
    second = patch_failed_execution(
        request, _evidence(request), backend=_backend(calls, status="completed")
    )
    assert first == second
    assert first["status"] == "timeout"
    assert first["error_type"] == "bounded_stop"
    assert first["error_message"] == "stopped before mutation"
    assert calls == ["patch-one"]


def test_crash_after_intent_becomes_outcome_unknown_without_retry(tmp_path):
    request = _request(tmp_path)

    class Lost(BaseException):
        pass

    try:
        patch_failed_execution(
            request,
            _evidence(request),
            backend=lambda *_: (_ for _ in ()).throw(Lost()),
        )
    except Lost:
        pass
    assert (request.output_dir / "intent.json").is_file()
    result = patch_failed_execution(
        request,
        _evidence(request),
        backend=lambda *_: (_ for _ in ()).throw(AssertionError("must not retry")),
    )
    assert result["status"] == "outcome_unknown"
