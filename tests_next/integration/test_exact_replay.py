from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from skillrace_next.pipeline import stages
from skillrace_next.pipeline.stages import replay
from skillrace_next.records import CheckResults, RunRecord
from skillrace_next.runtime.docker import CleanupResult, RunningContainer
from skillrace_next.storage import canonical_json_hash, tree_hash
from tests_next.unit.test_patch_evidence import patch_inputs
from tests_next.unit.test_test_cases import config_for


def test_replay_uses_fresh_run_and_exact_saved_check_scripts(tmp_path: Path) -> None:
    skill, test, failed_run, bundle, _ = patch_inputs(tmp_path)
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    (candidate_dir / "SKILL.md").write_text(
        (skill.directory_path / "SKILL.md").read_text() + "\nVerify exact output.\n",
        encoding="utf-8",
    )
    candidate = replace(
        skill,
        version_id="S1",
        parent_version_id="S0",
        directory_path=candidate_dir,
        tree_hash=tree_hash(candidate_dir),
    )
    captured = {}

    def fake_agent(skill_arg, test_arg, config_arg, output_dir):
        captured["agent"] = (skill_arg, test_arg, config_arg, Path(output_dir))
        output = Path(output_dir)
        artifact = output / "artifact"
        runtime = output / "runtime"
        artifact.mkdir(parents=True)
        runtime.mkdir()
        (artifact / "result.txt").write_text("ok\n", encoding="utf-8")
        paths = {}
        for name in ("trace.jsonl", "tool_outputs.jsonl", "stdout.txt", "stderr.txt"):
            paths[name] = runtime / name
            paths[name].write_text("\n", encoding="utf-8")
        now = datetime.now(UTC).isoformat()
        return RunRecord(
            run_id="fresh-replay-run",
            test_id=test_arg.test_id,
            skill_id=skill_arg.skill_id,
            skill_version_id=skill_arg.version_id,
            method=test_arg.origin_method,
            model_id=config_arg.model_id,
            budget=config_arg.role_budgets["weak_agent"],
            container_id="fresh-container",
            image_id=test_arg.container_image_id,
            started_at=now,
            ended_at=now,
            termination_status="completed",
            artifact_path=artifact,
            artifact_hash=tree_hash(artifact),
            trace_path=paths["trace.jsonl"],
            tool_log_path=paths["tool_outputs.jsonl"],
            stdout_path=paths["stdout.txt"],
            stderr_path=paths["stderr.txt"],
            provider_receipt_paths=(),
            cost_totals={"total_tokens": 5},
        )

    def fake_checks(container, artifact, rebound, output_dir):
        captured["container"] = container
        captured["bundle"] = rebound
        manifest = json.loads(rebound.manifest_path.read_text())
        result_path = Path(output_dir) / "check_results.json"
        result_path.parent.mkdir(parents=True)
        results = CheckResults(
            results_id="fresh-results",
            run_id="fresh-replay-run",
            check_bundle_hash=canonical_json_hash(manifest),
            artifact_hash_before=tree_hash(artifact),
            artifact_hash_after=tree_hash(artifact),
            artifact_unchanged=True,
            results=({"check_id": "P1-C1", "property_id": "P1", "status": "pass"},),
            results_path=result_path,
        )
        result_path.write_text(json.dumps(results.to_dict()) + "\n")
        return results

    config = config_for(tmp_path)
    results = replay(
        candidate,
        test,
        bundle,
        config,
        tmp_path / "replay",
        agent_runner=fake_agent,
        check_runner=fake_checks,
    )

    assert results.run_id == "fresh-replay-run"
    assert captured["agent"][:3] == (candidate, test, config)
    assert captured["container"] == RunningContainer(
        "fresh-container", "skillrace-replay-fresh-replay-run", test.container_image_id
    )
    rebound = captured["bundle"]
    assert rebound.run_id == "fresh-replay-run"
    assert rebound.artifact_hash != failed_run.artifact_hash
    assert rebound.script_paths[0].read_bytes() == bundle.script_paths[0].read_bytes()
    old_manifest = json.loads(bundle.manifest_path.read_text())
    new_manifest = json.loads(rebound.manifest_path.read_text())
    assert {key: value for key, value in new_manifest.items() if key not in {"run_id", "artifact_hash"}} == {
        key: value for key, value in old_manifest.items() if key not in {"run_id", "artifact_hash"}
    }


def test_replay_checks_partial_artifact_after_agent_timeout(tmp_path: Path) -> None:
    skill, test, _, bundle, _ = patch_inputs(tmp_path)
    checked: dict[str, object] = {}

    def timed_out_agent(skill_arg, test_arg, config_arg, output_dir):
        output = Path(output_dir)
        artifact = output / "artifact"
        runtime = output / "runtime"
        artifact.mkdir(parents=True)
        runtime.mkdir()
        (artifact / "partial.txt").write_text("unfinished\n", encoding="utf-8")
        paths = {}
        for name in ("trace.jsonl", "tool_outputs.jsonl", "stdout.txt", "stderr.txt"):
            paths[name] = runtime / name
            paths[name].write_text("\n", encoding="utf-8")
        now = datetime.now(UTC).isoformat()
        return RunRecord(
            run_id="timed-out-replay-run",
            test_id=test_arg.test_id,
            skill_id=skill_arg.skill_id,
            skill_version_id=skill_arg.version_id,
            method=test_arg.origin_method,
            model_id=config_arg.model_id,
            budget=config_arg.role_budgets["weak_agent"],
            container_id="timed-out-container",
            image_id=test_arg.container_image_id,
            started_at=now,
            ended_at=now,
            termination_status="agent_timeout",
            artifact_path=artifact,
            artifact_hash=tree_hash(artifact),
            trace_path=paths["trace.jsonl"],
            tool_log_path=paths["tool_outputs.jsonl"],
            stdout_path=paths["stdout.txt"],
            stderr_path=paths["stderr.txt"],
            provider_receipt_paths=(),
            cost_totals={"total_tokens": 3},
        )

    def check_partial(container, artifact, rebound, output_dir):
        checked["container"] = container
        checked["artifact"] = Path(artifact)
        manifest = json.loads(rebound.manifest_path.read_text(encoding="utf-8"))
        result_path = Path(output_dir) / "check_results.json"
        result_path.parent.mkdir(parents=True)
        results = CheckResults(
            results_id="timeout-results",
            run_id="timed-out-replay-run",
            check_bundle_hash=canonical_json_hash(manifest),
            artifact_hash_before=tree_hash(artifact),
            artifact_hash_after=tree_hash(artifact),
            artifact_unchanged=True,
            results=(
                {
                    "check_id": "P1-C1",
                    "property_id": "P1",
                    "status": "fail",
                    "diagnostic": "partial artifact did not satisfy the check",
                },
            ),
            results_path=result_path,
        )
        result_path.write_text(json.dumps(results.to_dict()) + "\n", encoding="utf-8")
        return results

    results = replay(
        skill,
        test,
        bundle,
        config_for(tmp_path),
        tmp_path / "timeout-replay",
        agent_runner=timed_out_agent,
        check_runner=check_partial,
    )

    assert results.results[0]["status"] == "fail"
    assert checked["container"] == RunningContainer(
        "timed-out-container",
        "skillrace-replay-timed-out-replay-run",
        test.container_image_id,
    )
    receipt = json.loads(
        (tmp_path / "timeout-replay" / "replay.json").read_text(encoding="utf-8")
    )
    assert receipt["termination_status"] == "agent_timeout"


def test_replay_removes_container_after_infrastructure_run_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill, test, failed_run, bundle, _ = patch_inputs(tmp_path)
    failed = replace(
        failed_run,
        run_id="provider-error-replay-run",
        container_id="provider-error-container",
        termination_status="provider_error",
    )
    removed: list[RunningContainer] = []

    def failed_agent(*args: object, **kwargs: object) -> RunRecord:
        return failed

    def fake_remove(container: RunningContainer) -> CleanupResult:
        removed.append(container)
        return CleanupResult(success=True, removed=True, stderr="")

    monkeypatch.setattr(stages, "remove_container", fake_remove)

    with pytest.raises(RuntimeError, match="provider_error"):
        replay(
            skill,
            test,
            bundle,
            config_for(tmp_path),
            tmp_path / "provider-error-replay",
            agent_runner=failed_agent,
        )

    assert removed == [
        RunningContainer(
            "provider-error-container",
            "skillrace-replay-provider-error-replay-run",
            failed.image_id,
        )
    ]
    cleanup = json.loads(
        (tmp_path / "provider-error-replay" / "cleanup.json").read_text(
            encoding="utf-8"
        )
    )
    assert cleanup["success"] is True
    assert cleanup["removed"] is True
