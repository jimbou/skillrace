from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from skillrace_next.runtime.docker import ContainerSpec, start_task_container
from skillrace_next.storage import atomic_write_json, tree_hash
from skillrace_next.verification.codex import validate_check_manifest
from skillrace_next.verification.executor import execute_checks


pytestmark = pytest.mark.live


def latest_real_codex_bundle() -> tuple[Path, Path]:
    root = Path("out/live-contracts/codex-verifier")
    for candidate in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
        workspace = candidate / "verifier_workspace"
        manifest_path = workspace / "output" / "check_manifest.json"
        events_path = workspace / "output" / "codex-events.jsonl"
        run_path = workspace / "input" / "run.json"
        nl_checks_path = workspace / "input" / "nl_checks.json"
        if not all(
            path.is_file()
            for path in (manifest_path, events_path, run_path, nl_checks_path)
        ):
            continue
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        if not any(event.get("type") == "thread.started" for event in events):
            continue
        run = json.loads(run_path.read_text(encoding="utf-8"))
        source_run = Path(run.get("source_task_run", ""))
        task_receipt = source_run / "runtime" / "exec.json"
        artifact = source_run / "artifact"
        if not task_receipt.is_file() or not artifact.is_dir():
            continue
        receipt = json.loads(task_receipt.read_text(encoding="utf-8"))
        if receipt.get("exit_code") != 0 or receipt.get("model") != "deepseek-v3.2":
            continue
        return candidate, source_run
    pytest.fail("a real Codex bundle over a real Yunwu task run is required")


def test_real_codex_bundle_executes_authoritatively_in_real_task_container(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("yunwu_key")
    if not secret:
        pytest.skip("yunwu_key is required to bind the real Yunwu prerequisite")
    codex_run, source_task_run = latest_real_codex_bundle()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "check-executor" / run_id
    artifact = evidence / "artifact"
    bundle_root = evidence / "check-bundle"
    shutil.copytree(source_task_run / "artifact", artifact)
    shutil.copytree(codex_run / "verifier_workspace" / "output", bundle_root)
    nl_checks = json.loads(
        (codex_run / "verifier_workspace" / "input" / "nl_checks.json").read_text(
            encoding="utf-8"
        )
    )
    bundle = validate_check_manifest(
        bundle_root / "check_manifest.json",
        nl_checks,
        tree_hash(artifact),
    )
    image = "skillrace-next/task-fixture:test"
    fixture = Path("tests_next/fixtures/task").resolve()
    subprocess.run(
        [
            "docker",
            "build",
            "--network=none",
            "-q",
            "-t",
            image,
            str(fixture),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    inspected = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    image_id = inspected.stdout.strip()
    atomic_write_json(
        evidence / "provenance.json",
        {
            "schema": "skillrace-live-check-provenance/1",
            "source_yunwu_task_run": str(source_task_run),
            "source_codex_run": str(codex_run),
            "yunwu_model": "deepseek-v3.2",
            "codex_model": "gpt-5.6-terra",
            "codex_reasoning": "medium",
            "docker_image_id": image_id,
        },
    )
    running = start_task_container(
        ContainerSpec(
            name="skillrace-next-live-check-" + uuid.uuid4().hex[:12],
            image=image,
            image_id=image_id,
            mounts=((artifact, "/workspace", "rw"),),
            network="none",
            cpus="1",
            memory="256m",
            working_directory="/workspace",
        )
    )

    results = execute_checks(running, artifact, bundle, evidence / "results")

    assert results.artifact_unchanged
    assert results.artifact_hash_before == results.artifact_hash_after
    assert results.results
    assert all(item["status"] == "pass" for item in results.results)
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert {item["property_id"] for item in manifest["checks"]} == {"P1", "P2"}
    assert all(
        item["argv"][1]
        in {item["script"], f"/tmp/skillrace-checks/{item['script']}"}
        for item in manifest["checks"]
    )
    for item in results.results:
        stdout = evidence / "results" / item["stdout_path"]
        payload = json.loads(stdout.read_text(encoding="utf-8"))
        assert payload["diagnostic"]
        assert isinstance(payload["evidence_paths"], list)
    stored = json.loads(results.results_path.read_text(encoding="utf-8"))
    assert stored["artifact_unchanged"] is True
    assert (evidence / "results" / "cleanup.json").is_file()
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
