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
    exec_task,
    remove_container,
    start_task_container,
)
from skillrace_next.storage import atomic_write_json


pytestmark = pytest.mark.live


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
        timeout=120,
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
