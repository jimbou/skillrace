import subprocess
from typing import Any

import pytest

from skillrace_next.runtime import docker
from skillrace_next.runtime.docker import RunningContainer, restore_mount_ownership


def test_restore_mount_ownership_uses_root_without_extra_container_privileges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(docker.subprocess, "run", fake_run)

    restore_mount_ownership(
        RunningContainer("container-1", "task", "sha256:image"),
        ("/workspace", "/evidence"),
        1000,
        1001,
    )

    assert captured["command"] == [
        "docker",
        "exec",
        "--user",
        "0",
        "container-1",
        "chown",
        "-R",
        "1000:1001",
        "/workspace",
        "/evidence",
    ]
    assert "--privileged" not in captured["command"]
    assert captured["kwargs"]["timeout"] == 30


def test_restore_mount_ownership_stops_on_chown_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        docker.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "permission denied"
        ),
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        restore_mount_ownership(
            RunningContainer("container-1", "task", "sha256:image"),
            ("/workspace",),
            1000,
            1000,
        )
