from pathlib import Path
import os
import subprocess
import uuid

import pytest

from skillrace_next.runtime.artifacts import freeze_artifact, verify_artifact_unchanged
from skillrace_next.runtime.docker import (
    ContainerSpec,
    copy_into_container,
    exec_task,
    remove_container,
    start_task_container,
)


@pytest.fixture(scope="module")
def task_image() -> tuple[str, str]:
    tag = "skillrace-next/task-fixture:test"
    fixture = Path("tests_next/fixtures/task").resolve()
    subprocess.run(
        ["docker", "build", "--network=none", "-q", "-t", tag, str(fixture)],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    inspected = subprocess.run(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return tag, inspected.stdout.strip()


def container_spec(
    task_image: tuple[str, str], artifact: Path, evidence: Path
) -> ContainerSpec:
    tag, image_id = task_image
    return ContainerSpec(
        name="skillrace-next-test-" + uuid.uuid4().hex[:12],
        image=tag,
        image_id=image_id,
        mounts=(
            (artifact, "/workspace", "rw"),
            (evidence, "/evidence", "rw"),
        ),
        network="none",
        cpus="1",
        memory="256m",
        working_directory="/workspace",
        user=f"{os.getuid()}:{os.getgid()}",
        environment=("SKILLRACE_NEXT_TEST_VALUE",),
    )


def test_start_exec_copy_capture_and_cleanup_reuses_built_image(
    tmp_path: Path, task_image: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILLRACE_NEXT_TEST_VALUE", "forwarded")
    artifact = tmp_path / "artifact"
    evidence = tmp_path / "evidence"
    artifact.mkdir()
    evidence.mkdir()
    running = start_task_container(container_spec(task_image, artifact, evidence))
    try:
        assert running.image_id == task_image[1]
        result = exec_task(
            running,
            [
                "node",
                "-e",
                "require('fs').writeFileSync('/workspace/result.txt', 'ok\\n')",
            ],
            timeout_seconds=10,
        )
        assert result.exit_code == 0
        assert not result.timed_out
        assert (artifact / "result.txt").read_text(encoding="utf-8") == "ok\n"
        forwarded = exec_task(
            running, ["printenv", "SKILLRACE_NEXT_TEST_VALUE"], timeout_seconds=10
        )
        assert forwarded.stdout == "forwarded\n"

        source = tmp_path / "copy.txt"
        source.write_text("copied\n", encoding="utf-8")
        copy_into_container(running, source, "/tmp/copied.txt")
        copied = exec_task(running, ["cat", "/tmp/copied.txt"], timeout_seconds=10)
        assert copied.stdout == "copied\n"
    finally:
        cleanup = remove_container(running)

    assert cleanup.success
    assert cleanup.removed
    assert remove_container(running).success


def test_timeout_kills_child_but_preserves_container_and_partial_artifact(
    tmp_path: Path, task_image: tuple[str, str]
) -> None:
    artifact = tmp_path / "artifact"
    evidence = tmp_path / "evidence"
    artifact.mkdir()
    evidence.mkdir()
    running = start_task_container(container_spec(task_image, artifact, evidence))
    try:
        result = exec_task(
            running,
            [
                "node",
                "-e",
                "const fs = require('fs'); "
                "fs.writeFileSync('/workspace/partial.txt', 'partial\\n'); "
                "setTimeout(() => fs.writeFileSync('/workspace/done.txt', 'done\\n'), 30000)",
            ],
            timeout_seconds=1,
        )

        assert result.timed_out
        assert (artifact / "partial.txt").read_text(encoding="utf-8") == "partial\n"
        assert not (artifact / "done.txt").exists()
        state = subprocess.run(
            ["docker", "inspect", running.container_id, "--format", "{{.State.Running}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert state.stdout.strip() == "true"

        frozen = freeze_artifact(artifact, checker_uid=65534)
        assert verify_artifact_unchanged(frozen)
    finally:
        cleanup = remove_container(running)

    assert cleanup.success
    assert cleanup.removed
