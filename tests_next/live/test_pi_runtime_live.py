from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from skillrace_next.runtime.pi import (
    PI_RUNTIME_IMAGE,
    PiRequest,
    direct_yunwu_preflight,
    run_pi,
)
from skillrace_next.storage import atomic_write_json


pytestmark = pytest.mark.live


def test_generic_pi_runtime_image_is_rebuilt_from_pinned_local_image(
    live_evidence_root: Path,
) -> None:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "pi-runtime-image" / run_id
    evidence.mkdir(parents=True)
    legacy_tag = "skillrace/pi-base:0.73.1-deepseek-v3.2"
    inspected_legacy = subprocess.run(
        ["docker", "image", "inspect", legacy_tag],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    legacy = json.loads(inspected_legacy.stdout)[0]
    legacy_id = legacy["Id"]
    source_tag = "skillrace/pi-runtime-source:" + legacy_id.removeprefix("sha256:")[:12]
    subprocess.run(
        ["docker", "image", "tag", legacy_id, source_tag],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    built = subprocess.run(
        [
            "docker",
            "build",
            "--network=none",
            "--build-arg",
            f"SOURCE_IMAGE={source_tag}",
            "-q",
            "-t",
            PI_RUNTIME_IMAGE,
            "-f",
            "skillrace_next/runtime/Dockerfile.pi-runtime",
            "skillrace_next/runtime",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    inspected_runtime = subprocess.run(
        ["docker", "image", "inspect", PI_RUNTIME_IMAGE],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    runtime = json.loads(inspected_runtime.stdout)[0]
    runtime_id = runtime["Id"]
    labels = runtime["Config"]["Labels"]
    atomic_write_json(
        evidence / "runtime-image.json",
        {
            "schema": "skillrace-runtime-image/1",
            "source_tag": legacy_tag,
            "source_image_id": legacy_id,
            "hashed_source_tag": source_tag,
            "runtime_tag": PI_RUNTIME_IMAGE,
            "runtime_image_id": runtime_id,
            "runtime_labels": labels,
            "build_stdout": built.stdout.strip(),
        },
    )

    assert runtime_id != legacy_id
    assert labels["org.skillrace.track.model"] == "runtime-mounted"
    assert all(
        model not in json.dumps({"tag": PI_RUNTIME_IMAGE, "labels": labels}).lower()
        for model in ("deepseek", "qwen", "glm")
    )


def test_real_yunwu_preflight_and_pi_tool_call(
    live_evidence_root: Path,
) -> None:
    secret = os.environ.get("yunwu_key")
    if not secret:
        pytest.skip("yunwu_key is required for the live contract")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    evidence = live_evidence_root / "pi-runtime" / run_id
    direct_dir = evidence / "direct"
    pi_dir = evidence / "pi"
    workspace = evidence / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("skillrace-live-probe\n", encoding="utf-8")
    prompt = evidence / "prompt.txt"
    prompt.write_text(
        "Use the read tool to read /workspace/input.txt. Then use the write tool to "
        "create /workspace/output.txt containing exactly SKILLRACE_PI_TOOL_OK followed "
        "by one newline. You must perform both tool calls, then stop.\n",
        encoding="utf-8",
    )

    probe = direct_yunwu_preflight("deepseek-v3.2", direct_dir)
    assert probe.status == "completed", probe.receipt_path
    assert probe.model == "deepseek-v3.2"
    assert "SKILLRACE_PREFLIGHT_OK" in probe.content
    assert probe.usage

    result = run_pi(
        PiRequest(
            operation_id=f"pi-runtime.{run_id}",
            model="deepseek-v3.2",
            prompt_path=prompt,
            output_dir=pi_dir,
            image=PI_RUNTIME_IMAGE,
            allowed_tools=("read", "write"),
            max_turns=4,
            timeout_seconds=180,
            mounts=((workspace, "/workspace", "rw"),),
        )
    )

    assert result.status == "completed", result.receipt_path
    assert result.model == "deepseek-v3.2"
    assert result.trace_path.is_file()
    assert result.usage.get("input_tokens", 0) > 0
    assert result.usage.get("output_tokens", 0) > 0
    assert result.usage.get("turns", 0) <= 4
    assert result.usage.get("model", "deepseek-v3.2") == "deepseek-v3.2"
    assert (workspace / "output.txt").read_text(encoding="utf-8") == (
        "SKILLRACE_PI_TOOL_OK\n"
    )

    event_path = pi_dir / "accounting" / "tool-events.jsonl"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    tool_names = [event["tool"] for event in events if event.get("type") == "tool_call"]
    assert "read" in tool_names
    assert "write" in tool_names

    for path in evidence.rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8", errors="replace")
