from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import uuid

import pytest

from skillrace_next.pipeline.stages import accept_patch, replay
from skillrace_next.records import CheckResults, SkillVersion, TestCase as CaseRecord
from skillrace_next.storage import atomic_write_json, tree_hash
from skillrace_next.verification.codex import validate_check_manifest
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


def accepted_patch_source(model: str) -> Path:
    root = Path("out/live-contracts/patcher") / model
    for candidate in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
        attempt_path = candidate / "patch" / "patch-attempt.json"
        result_path = candidate / "check-results" / "check_results.json"
        if not attempt_path.is_file() or not result_path.is_file():
            continue
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
        results = json.loads(result_path.read_text(encoding="utf-8"))
        if attempt.get("patch_status") == "patched" and any(
            item.get("status") == "fail" for item in results.get("results", [])
        ):
            return candidate
    pytest.fail("a successful real Task 12 patch over a real failure is required")


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "qwen3.6-flash"])
def test_real_patched_skill_runs_in_fresh_lab_container_with_exact_checks(
    model: str, live_evidence_root: Path,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the exact replay contract")
    source = accepted_patch_source(model)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "exact-replay" / model / run_id
    evidence.mkdir(parents=True)
    test = CaseRecord.from_dict(
        json.loads((source / "test-case.json").read_text(encoding="utf-8"))
    )
    attempt = json.loads(
        (source / "patch" / "patch-attempt.json").read_text(encoding="utf-8")
    )
    candidate_dir = source / "patch" / "candidate"
    skill = SkillVersion(
        skill_id="live-median-calculation",
        version_id="S1",
        parent_version_id="S0",
        directory_path=candidate_dir,
        tree_hash=tree_hash(candidate_dir),
        creation_role="patcher",
        model_id=model,
        receipt_path=Path(attempt["cost_receipt_path"]),
    )
    nl_checks = json.loads(test.nl_check_path.read_text(encoding="utf-8"))
    original_artifact = source / "weak-run" / "artifact"
    bundle = validate_check_manifest(
        source / "verifier" / "output" / "check_manifest.json",
        nl_checks,
        tree_hash(original_artifact),
    )
    before = CheckResults.from_dict(
        json.loads((source / "check-results" / "check_results.json").read_text())
    )
    base_config = live_config(evidence, {"weak_agent": 4})
    config = replace(
        base_config,
        experiment_id="live-exact-replay",
        methods=("random",),
        network_policy="host",
        provider="lab",
        model_id=model,
        timeouts={**base_config.timeouts, "pi": 240},
    )

    replay_results = replay(skill, test, bundle, config, evidence / "replay")
    decision = accept_patch(before.results, replay_results.results, [])
    atomic_write_json(
        evidence / "decision.json",
        {
            "schema": "skillrace-patch-decision/1",
            "source_patch": str(source),
            "before_results_id": before.results_id,
            "replay_results_id": replay_results.results_id,
            "decision": decision,
        },
    )

    assert decision == "accepted"
    assert all(item["status"] == "pass" for item in replay_results.results)
    replay_run = json.loads((evidence / "replay" / "run" / "run.json").read_text())
    assert replay_run["run_id"] != before.run_id
    assert replay_run["container_id"]
    assert replay_run["model_id"] == model
    assert replay_run["skill_version_id"] == "S1"
    original_scripts = {path.name: path.read_bytes() for path in bundle.script_paths}
    replay_scripts = {
        path.name: path.read_bytes()
        for path in (evidence / "replay" / "check-bundle" / "checks").iterdir()
    }
    assert replay_scripts == original_scripts
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
