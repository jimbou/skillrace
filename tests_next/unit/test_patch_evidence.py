from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path

from skillrace_next.pipeline.stages import build_patch_evidence
from skillrace_next.records import (
    CheckBundle,
    CheckResults,
    RunRecord,
    SkillVersion,
)
from skillrace_next.storage import canonical_json_hash, tree_hash
from tests_next.unit.test_test_cases import pending_test


def patch_inputs(root: Path):
    skill_dir = root / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: fixture\ndescription: Fixture skill.\n---\n# Fixture\nVerify output.\n",
        encoding="utf-8",
    )
    skill_receipt = root / "skill-receipt.json"
    skill_receipt.write_text("{}\n", encoding="utf-8")
    skill = SkillVersion(
        skill_id="skill-1",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v3.2",
        receipt_path=skill_receipt,
    )
    test = replace(
        pending_test(root),
        test_id="test-1",
        origin_method="random",
        validation_status="valid",
        validation_diagnostic="validated",
        container_image_id="sha256:test-image",
    )
    run_root = root / "run"
    artifact = run_root / "artifact"
    runtime = run_root / "runtime"
    artifact.mkdir(parents=True)
    runtime.mkdir()
    (artifact / "result.txt").write_text("wrong\n", encoding="utf-8")
    trace = runtime / "trace.jsonl"
    trace.write_text('{"message":{"role":"assistant"}}\n', encoding="utf-8")
    tool_log = runtime / "tool_outputs.jsonl"
    tool_log.write_text('{"tool":"write"}\n', encoding="utf-8")
    stdout = runtime / "stdout.txt"
    stderr = runtime / "stderr.txt"
    stdout.write_text("task complete\n", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    run = RunRecord(
        run_id="run-1",
        test_id=test.test_id,
        skill_id=skill.skill_id,
        skill_version_id=skill.version_id,
        method="random",
        model_id="deepseek-v3.2",
        budget=4,
        container_id="container-1",
        image_id="sha256:test-image",
        started_at=datetime.now(UTC).isoformat(),
        ended_at=datetime.now(UTC).isoformat(),
        termination_status="completed",
        artifact_path=artifact,
        artifact_hash=tree_hash(artifact),
        trace_path=trace,
        tool_log_path=tool_log,
        stdout_path=stdout,
        stderr_path=stderr,
        provider_receipt_paths=(),
        cost_totals={"total_tokens": 10},
    )
    checks = root / "checks"
    scripts = checks / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "P1-C1.py"
    script.write_text("print('authoritative checker')\n", encoding="utf-8")
    manifest_value = {
        "schema": "skillrace-check-bundle/1",
        "run_id": run.run_id,
        "artifact_hash": run.artifact_hash,
        "checks": [
            {
                "check_id": "P1-C1",
                "property_id": "P1",
                "script": "checks/P1-C1.py",
                "argv": ["python", "checks/P1-C1.py"],
                "timeout_seconds": 30,
                "purpose": "check result",
                "pass_condition": "exact output",
                "failure_condition": "wrong output",
                "root_cause_category": "format_contract",
            }
        ],
        "uncovered": [],
    }
    manifest = checks / "check_manifest.json"
    manifest.write_text(json.dumps(manifest_value, sort_keys=True) + "\n", encoding="utf-8")
    codex_receipt = checks / "codex-events.jsonl"
    codex_receipt.write_text('{"type":"thread.started"}\n', encoding="utf-8")
    bundle = CheckBundle(
        bundle_id="bundle-1",
        run_id=run.run_id,
        artifact_hash=run.artifact_hash,
        input_hashes={"artifact": run.artifact_hash},
        manifest_path=manifest,
        script_paths=(script,),
        codex_receipt_path=codex_receipt,
    )
    result_root = root / "results"
    outputs = result_root / "outputs"
    outputs.mkdir(parents=True)
    result_stdout = outputs / "P1-C1.stdout"
    result_stderr = outputs / "P1-C1.stderr"
    result_stdout.write_text(
        '{"diagnostic":"expected ok, found wrong","evidence_paths":["result.txt"]}\n',
        encoding="utf-8",
    )
    result_stderr.write_text("", encoding="utf-8")
    result_items = (
        {
            "check_id": "P1-C1",
            "property_id": "P1",
            "status": "fail",
            "exit_code": 1,
            "duration_seconds": 0.1,
            "diagnostic": "expected ok, found wrong",
            "stdout_path": "outputs/P1-C1.stdout",
            "stderr_path": "outputs/P1-C1.stderr",
            "evidence_paths": ["result.txt"],
        },
    )
    results_path = result_root / "check_results.json"
    results = CheckResults(
        results_id="results-1",
        run_id=run.run_id,
        check_bundle_hash=canonical_json_hash(manifest_value),
        artifact_hash_before=run.artifact_hash,
        artifact_hash_after=run.artifact_hash,
        artifact_unchanged=True,
        results=result_items,
        results_path=results_path,
    )
    results_path.write_text(json.dumps(results.to_dict(), sort_keys=True) + "\n", encoding="utf-8")
    return skill, test, run, bundle, results


def test_common_patch_evidence_is_identical_and_method_additions_are_exact(
    tmp_path: Path,
) -> None:
    skill, test, run, bundle, results = patch_inputs(tmp_path)
    verigrey_state = {
        "last_observation": {
            "sequence": [{"tool": "write", "arguments": {"path": "string"}}],
            "novelty_delta": {"tools": [], "transitions": [], "sequence": False},
            "coverage_counts": {"tools": [2], "transitions": [], "sequence": 2},
        }
    }
    skillrace_state = {
        "episodes": [{"episode_id": "episode-1", "purpose": "write output"}],
        "tree": {"schema": "skillrace-reasoning-tree/1", "nodes": [], "edges": []},
        "branch": {"node_id": "missing-validation", "reach_status": "unreached"},
    }
    built = {}
    for method, state in (
        ("random", {}),
        ("verigrey", verigrey_state),
        ("skillrace", skillrace_state),
    ):
        built[method] = build_patch_evidence(
            method,
            state,
            skill,
            test,
            run,
            bundle,
            results,
            tmp_path / f"evidence-{method}",
        )

    common_hashes = {tree_hash(path / "common") for path, _ in built.values()}
    assert len(common_hashes) == 1
    random_path, random_hash = built["random"]
    assert not (random_path / "method").exists()
    index = json.loads((random_path / "evidence.json").read_text())
    assert index["task_prompt"] == "Create result.txt containing ok.\n"
    assert index["authoritative_results"] == list(results.results)
    assert index["files"] == {
        "skill": "common/skill/SKILL.md",
        "test_prompt": "common/test/prompt.txt",
        "environment": "common/test/environment",
        "artifact": "common/artifact",
        "trace": "common/run/trace.jsonl",
        "tool_outputs": "common/run/tool_outputs.jsonl",
        "nl_checks": "common/test/nl_checks.json",
        "check_manifest": "common/checks/check_manifest.json",
        "check_scripts": ["common/checks/scripts/P1-C1.py"],
        "check_results": "common/results/check_results.json",
        "result_streams": [
            "common/results/outputs/P1-C1.stderr",
            "common/results/outputs/P1-C1.stdout",
        ],
        "method": None,
    }
    verigrey_path, verigrey_hash = built["verigrey"]
    assert json.loads((verigrey_path / "method" / "verigrey.json").read_text()) == verigrey_state["last_observation"]
    skillrace_path, skillrace_hash = built["skillrace"]
    assert json.loads((skillrace_path / "method" / "skillrace.json").read_text()) == skillrace_state
    assert len({random_hash, verigrey_hash, skillrace_hash}) == 3
    common = random_path / "common"
    assert (common / "skill" / "SKILL.md").read_bytes() == (skill.directory_path / "SKILL.md").read_bytes()
    assert (common / "test" / "prompt.txt").read_bytes() == test.prompt_path.read_bytes()
    assert (common / "test" / "environment" / "Dockerfile").read_bytes() == (test.environment_directory / "Dockerfile").read_bytes()
    assert (common / "checks" / "check_manifest.json").read_bytes() == bundle.manifest_path.read_bytes()
    assert (common / "checks" / "scripts" / "P1-C1.py").read_bytes() == bundle.script_paths[0].read_bytes()
    assert (common / "results" / "outputs" / "P1-C1.stdout").read_bytes() == (results.results_path.parent / "outputs/P1-C1.stdout").read_bytes()
    assert json.loads((common / "run" / "run.json").read_text()) == run.to_dict()
    for path, bundle_hash in built.values():
        assert bundle_hash == tree_hash(path)
        assert all(candidate.stat().st_mode & 0o222 == 0 for candidate in [path, *path.rglob("*")])


def test_patch_evidence_rejects_mismatched_authoritative_results(tmp_path: Path) -> None:
    skill, test, run, bundle, results = patch_inputs(tmp_path)
    mismatched = replace(results, check_bundle_hash="wrong")

    try:
        build_patch_evidence(
            "random", {}, skill, test, run, bundle, mismatched, tmp_path / "bad"
        )
    except ValueError as error:
        assert "check bundle" in str(error)
    else:
        raise AssertionError("mismatched checker provenance must be rejected")
