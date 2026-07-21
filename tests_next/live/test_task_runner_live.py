from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from skillrace_next.runtime.artifacts import freeze_artifact, verify_artifact_unchanged
from skillrace_next.runtime.docker import (
    ContainerSpec,
    RunningContainer,
    exec_task,
    remove_container,
    start_task_container,
)
from skillrace_next.pipeline.stages import run_agent
from skillrace_next.records import SkillVersion, TestCase as CaseRecord
from skillrace_next.runtime.providers import resolve_model, write_pi_models
from skillrace_next.storage import atomic_write_json, file_hash, tree_hash
from tests_next.live.test_tree_merge_live import live_config


pytestmark = pytest.mark.live


def test_real_weak_agent_runs_as_root_and_restores_host_ownership(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.fail("LAB_KEY_UNLIMITED is required for the root task-agent contract")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "task-runner-root" / run_id
    evidence.mkdir(parents=True)
    image = "skillrace-next/task-fixture:test"
    subprocess.run(
        [
            "docker",
            "build",
            "-q",
            "-t",
            image,
            str(Path("tests_next/fixtures/task").resolve()),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    image_id = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    skill_dir = evidence / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "# Verify effective user\nRun `id -u`, save its exact output, then read it back.\n",
        encoding="utf-8",
    )
    skill_receipt = evidence / "skill-receipt.json"
    atomic_write_json(skill_receipt, {"source": "root live contract"})
    skill = SkillVersion(
        skill_id="root-inspection",
        version_id="S0",
        parent_version_id=None,
        directory_path=skill_dir,
        tree_hash=tree_hash(skill_dir),
        creation_role="fixture",
        model_id="deepseek-v4-flash",
        receipt_path=skill_receipt,
    )
    case = evidence / "case"
    environment = case / "environment"
    environment.mkdir(parents=True)
    prompt_path = case / "prompt.txt"
    prompt_path.write_text(
        "Run `id -u` in the task container and write only its numeric output followed by "
        "one newline to /workspace/uid.txt. Then read /workspace/uid.txt back.\n",
        encoding="utf-8",
    )
    checks_path = case / "nl_checks.json"
    atomic_write_json(
        checks_path,
        [
            {
                "property_id": "P1",
                "description": "uid.txt contains the effective numeric user ID.",
            }
        ],
    )
    (environment / "Dockerfile").write_text(
        f"FROM {image}\nWORKDIR /workspace\n", encoding="utf-8"
    )
    atomic_write_json(environment / "sanity.json", {"status": "pass"})
    proposal_receipt = case / "proposal.json"
    atomic_write_json(proposal_receipt, {"source": "root live contract"})
    test = CaseRecord(
        test_id="root-live-test",
        prompt_path=prompt_path,
        prompt_hash=file_hash(prompt_path),
        environment_directory=environment,
        environment_hash=tree_hash(environment),
        nl_check_path=checks_path,
        nl_check_hash=file_hash(checks_path),
        origin_method="random",
        proposal_receipt=proposal_receipt,
        validation_status="valid",
        validation_diagnostic="validated by pinned image ID",
        container_image_id=image_id,
    )
    base_config = live_config(evidence, {"weak_agent": 4})
    config = replace(
        base_config,
        experiment_id="live-root-task-agent",
        provider="lab",
        model_id="deepseek-v4-flash",
        network_policy="host",
        output_root=evidence,
        timeouts={**base_config.timeouts, "pi": 240, "docker": 600},
    )

    record = run_agent(skill, test, config, evidence / "run")
    try:
        inspection = subprocess.run(
            ["docker", "inspect", record.container_id],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        container = json.loads(inspection.stdout)[0]
        mounts = container["Mounts"]
        assert container["Config"]["User"] == "0:0"
        assert next(item for item in mounts if item["Destination"] == "/skill")["RW"] is False
        assert all("docker.sock" not in item["Source"] for item in mounts)
    finally:
        cleanup = remove_container(
            RunningContainer(record.container_id, "root-task-agent", record.image_id)
        )
        atomic_write_json(
            evidence / "cleanup.json",
            {"success": cleanup.success, "removed": cleanup.removed, "stderr": cleanup.stderr},
        )

    assert record.termination_status == "completed"
    assert (record.artifact_path / "uid.txt").read_text(encoding="utf-8") == "0\n"
    ownership = json.loads(
        (evidence / "run" / "runtime" / "ownership.json").read_text(encoding="utf-8")
    )
    assert ownership["success"] is True
    assert (record.artifact_path / "uid.txt").stat().st_uid == os.getuid()
    assert record.trace_path.stat().st_uid == os.getuid()
    assert cleanup.success and cleanup.removed
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")


def test_real_lab_task_container_preserves_baked_workspace(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("LAB_KEY_UNLIMITED")
    if not secret:
        pytest.skip("LAB_KEY_UNLIMITED is required for the live contract")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "task-runner-seeded" / run_id
    artifact = evidence / "artifact"
    runtime_evidence = evidence / "runtime"
    artifact.mkdir(parents=True)
    runtime_evidence.mkdir()
    image = "skillrace-next/task-fixture:test"
    fixture = Path("tests_next/fixtures/task").resolve()
    subprocess.run(
        ["docker", "build", "-q", "-t", image, str(fixture)],
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
    selected = resolve_model("lab", "deepseek-v4-flash")
    models_path = write_pi_models(runtime_evidence / "models.json", selected)
    running = start_task_container(
        ContainerSpec(
            name="skillrace-next-live-" + uuid.uuid4().hex[:12],
            image=image,
            image_id=image_id,
            mounts=(
                (artifact, "/workspace", "rw"),
                (runtime_evidence, "/evidence", "rw"),
                (models_path, "/home/node/.pi/agent/models.json", "ro"),
            ),
            network="host",
            cpus="1",
            memory="512m",
            working_directory="/workspace",
            user=f"{os.getuid()}:{os.getgid()}",
            environment=(selected.key_environment,),
            seed_working_directory=True,
        )
    )
    try:
        result = exec_task(
            running,
            [
                "pi",
                "--provider",
                selected.provider,
                "--model",
                selected.upstream_model,
                "--thinking",
                "medium",
                "--print",
                "--tools",
                "read,write",
                "--no-extensions",
                "--no-skills",
                "--no-prompt-templates",
                "--no-themes",
                "--session",
                "/evidence/trace.jsonl",
                "Read /workspace/initial.txt, then create /workspace/seed-result.txt "
                "containing exactly the same text. Do not modify initial.txt.",
            ],
            timeout_seconds=240,
        )
        (runtime_evidence / "stdout.txt").write_text(
            result.stdout.replace(secret, "[REDACTED]"), encoding="utf-8"
        )
        (runtime_evidence / "stderr.txt").write_text(
            result.stderr.replace(secret, "[REDACTED]"), encoding="utf-8"
        )
        atomic_write_json(
            runtime_evidence / "exec.json",
            {
                "schema": "skillrace-task-exec/1",
                "model": selected.friendly_model,
                "image_id": image_id,
                "container_id": running.container_id,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_seconds": result.duration_seconds,
                "timeout_seconds": 240,
            },
        )
    finally:
        cleanup = remove_container(running)
        atomic_write_json(
            runtime_evidence / "cleanup.json",
            {
                "schema": "skillrace-container-cleanup/1",
                "container_id": running.container_id,
                "success": cleanup.success,
                "removed": cleanup.removed,
                "stderr": cleanup.stderr.replace(secret, "[REDACTED]"),
            },
        )

    assert result.exit_code == 0
    assert not result.timed_out
    assert (artifact / "initial.txt").read_text(encoding="utf-8") == "from-image\n"
    assert (artifact / "seed-result.txt").read_text(encoding="utf-8") == "from-image\n"
    assert cleanup.success and cleanup.removed
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")


def test_real_task_container_preserves_weak_agent_artifact_and_trace(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("yunwu_key")
    if not secret:
        pytest.skip("yunwu_key is required for the live contract")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "task-runner" / run_id
    artifact = evidence / "artifact"
    runtime_evidence = evidence / "runtime"
    artifact.mkdir(parents=True)
    runtime_evidence.mkdir()

    image = "skillrace-next/task-fixture:test"
    fixture = Path("tests_next/fixtures/task").resolve()
    subprocess.run(
        ["docker", "build", "-q", "-t", image, str(fixture)],
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
    operation_id = f"task-runner.{run_id}"
    prompt = (
        "Use the write tool to create /workspace/task-result.txt containing exactly "
        "SKILLRACE_TASK_AGENT_OK with no surrounding whitespace. Then use the read "
        "tool to read that file back. You must perform both tool calls and then stop."
    )
    running = start_task_container(
        ContainerSpec(
            name="skillrace-next-live-" + uuid.uuid4().hex[:12],
            image=image,
            image_id=image_id,
            mounts=(
                (artifact, "/workspace", "rw"),
                (runtime_evidence, "/evidence", "rw"),
            ),
            network="host",
            cpus="1",
            memory="512m",
            working_directory="/workspace",
            user=f"{os.getuid()}:{os.getgid()}",
            environment=("yunwu_key",),
        )
    )
    cleanup = None
    try:
        result = exec_task(
            running,
            [
                "pi",
                "--provider",
                "yunwu",
                "--model",
                "deepseek-v3.2",
                "--thinking",
                "medium",
                "--print",
                "--tools",
                "read,write",
                "--no-extensions",
                "--no-skills",
                "--no-prompt-templates",
                "--no-themes",
                "--session",
                "/evidence/trace.jsonl",
                prompt,
            ],
            timeout_seconds=180,
        )
        stdout = result.stdout.replace(secret, "[REDACTED]")
        stderr = result.stderr.replace(secret, "[REDACTED]")
        (runtime_evidence / "stdout.txt").write_text(stdout, encoding="utf-8")
        (runtime_evidence / "stderr.txt").write_text(stderr, encoding="utf-8")
        atomic_write_json(
            runtime_evidence / "exec.json",
            {
                "schema": "skillrace-task-exec/1",
                "operation_id": operation_id,
                "model": "deepseek-v3.2",
                "image_id": image_id,
                "container_id": running.container_id,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_seconds": result.duration_seconds,
                "timeout_seconds": 180,
            },
        )

        trace_path = runtime_evidence / "trace.jsonl"
        records = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        tool_outputs = [
            record
            for record in records
            if record.get("type") == "message"
            and record.get("message", {}).get("role") == "toolResult"
        ]
        (runtime_evidence / "tool_outputs.jsonl").write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in tool_outputs),
            encoding="utf-8",
        )
        assistant_messages = [
            record["message"]
            for record in records
            if record.get("type") == "message"
            and record.get("message", {}).get("role") == "assistant"
        ]
        usage = {
            "model": "deepseek-v3.2",
            "input_tokens": sum(
                int((message.get("usage") or {}).get("input", 0) or 0)
                for message in assistant_messages
            ),
            "output_tokens": sum(
                int((message.get("usage") or {}).get("output", 0) or 0)
                for message in assistant_messages
            ),
            "cache_read_tokens": sum(
                int((message.get("usage") or {}).get("cacheRead", 0) or 0)
                for message in assistant_messages
            ),
            "turns": len(assistant_messages),
            "provider_credits": "unpriced",
        }
        atomic_write_json(runtime_evidence / "usage.json", usage)
        frozen = freeze_artifact(artifact, checker_uid=65534)
        atomic_write_json(
            runtime_evidence / "artifact.json",
            {
                "schema": "skillrace-frozen-artifact/1",
                "path": str(frozen.path),
                "tree_hash": frozen.tree_hash,
                "checker_uid": frozen.checker_uid,
            },
        )
    finally:
        cleanup = remove_container(running)
        atomic_write_json(
            runtime_evidence / "cleanup.json",
            {
                "schema": "skillrace-container-cleanup/1",
                "container_id": running.container_id,
                "success": cleanup.success,
                "removed": cleanup.removed,
                "stderr": cleanup.stderr.replace(secret, "[REDACTED]"),
            },
        )

    assert result.exit_code == 0
    assert not result.timed_out
    assert (artifact / "task-result.txt").read_text(encoding="utf-8") == (
        "SKILLRACE_TASK_AGENT_OK"
    )
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert usage["turns"] <= 4
    assert all(message.get("model") == "deepseek-v3.2" for message in assistant_messages)
    tool_names = {record["message"].get("toolName") for record in tool_outputs}
    assert {"write", "read"} <= tool_names
    assert verify_artifact_unchanged(frozen)
    assert cleanup is not None and cleanup.success and cleanup.removed
    absent = subprocess.run(
        ["docker", "inspect", running.container_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert absent.returncode != 0
    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
