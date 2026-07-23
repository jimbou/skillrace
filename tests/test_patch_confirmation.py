from __future__ import annotations

from skillrace.io_utils import canonical_json_hash
from skillrace.patch_confirmation import confirm_patched_execution
from skillrace.patch_only import patch_failed_execution
from skillrace.repair_validation import FailureRepairRequest
from skillrace.revise_skill import package_hash


def test_confirmation_is_a_separate_exact_replay_after_completed_patch(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    (original / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    case = tmp_path / "case"; case.mkdir()
    run = tmp_path / "run"; run.mkdir()
    request = FailureRepairRequest(
        method="random", skill_name="demo", execution_id="e1", attempt_id="a1",
        candidate_id="c1", case_dir=case, original_skill_dir=original,
        original_skill_hash=package_hash(original), failed_property_ids=("p",),
        failure_signatures=("a" * 64,), run_dir=run,
        output_dir=tmp_path / "patch", repair_id="repair-one",
    )
    payload = {"schema": "skillrace-failure-repair-evidence/1",
               "original_skill_hash": request.original_skill_hash,
               "failure_core": {}, "method_evidence": {}}
    evidence = {"repair_id": request.repair_id, "reviser_payload": payload,
                "evidence_hash": canonical_json_hash(payload)}

    def backend(req, _evidence, work):
        skill = work / "skill"; skill.mkdir()
        (skill / "SKILL.md").write_text("# Fixed\n", encoding="utf-8")
        return {"status": "completed", "skill_dir": str(skill), "backend": "direct"}
    backend.backend_name = "direct"; backend.model = "model"; backend.timeout_seconds = 120
    patch_failed_execution(request, evidence, backend=backend)
    calls = []

    def executor(observed, skill, replay_dir):
        calls.append((observed.case_dir, skill, replay_dir))
        return {"status": "completed", "verdicts": [
            {"property_id": "p", "holds": True, "violated": False}
        ]}

    result = confirm_patched_execution(
        request, patch_dir=request.output_dir,
        output_dir=tmp_path / "confirmation", executor=executor,
    )
    assert result["status"] == "repair_confirmed"
    assert len(calls) == 1
    assert calls[0][0] == case
    assert calls[0][1] == request.output_dir / "skill"
    resumed = confirm_patched_execution(
        request, patch_dir=request.output_dir,
        output_dir=tmp_path / "confirmation",
        executor=lambda *_: (_ for _ in ()).throw(AssertionError("must not replay")),
    )
    assert resumed == result

