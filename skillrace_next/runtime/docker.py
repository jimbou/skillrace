from dataclasses import dataclass
from pathlib import Path
import subprocess
import time


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    image: str
    image_id: str
    mounts: tuple[tuple[Path, str, str], ...]
    network: str
    cpus: str
    memory: str
    working_directory: str
    user: str | None = None
    environment: tuple[str, ...] = ()
    seed_working_directory: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.image or not self.image_id:
            raise ValueError("container name, image, and image_id are required")
        if any(mode not in {"ro", "rw"} for _, _, mode in self.mounts):
            raise ValueError("mount mode must be ro or rw")
        if any(not source.exists() for source, _, _ in self.mounts):
            raise ValueError("every mount source must exist")
        if any(not name or "=" in name for name in self.environment):
            raise ValueError("environment entries must be variable names")
        if not isinstance(self.seed_working_directory, bool):
            raise ValueError("seed_working_directory must be boolean")


@dataclass(frozen=True)
class RunningContainer:
    container_id: str
    name: str
    image_id: str


@dataclass(frozen=True)
class ExecResult:
    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool


@dataclass(frozen=True)
class CleanupResult:
    success: bool
    removed: bool
    stderr: str


def start_task_container(spec: ContainerSpec) -> RunningContainer:
    inspected = subprocess.run(
        ["docker", "image", "inspect", spec.image, "--format", "{{.Id}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    resolved_image_id = inspected.stdout.strip()
    if resolved_image_id != spec.image_id:
        raise RuntimeError("container image ID differs from validated image ID")
    if spec.seed_working_directory:
        workspace_mounts = [
            source
            for source, destination, mode in spec.mounts
            if destination.rstrip("/") == spec.working_directory.rstrip("/")
            and mode == "rw"
        ]
        if len(workspace_mounts) != 1:
            raise ValueError("seeding requires one writable working-directory mount")
        workspace = workspace_mounts[0]
        if any(workspace.iterdir()):
            raise ValueError("seeded working-directory mount must start empty")
        created = subprocess.run(
            ["docker", "create", resolved_image_id],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        seed_container_id = created.stdout.strip()
        try:
            subprocess.run(
                [
                    "docker",
                    "cp",
                    f"{seed_container_id}:{spec.working_directory.rstrip('/')}/.",
                    str(workspace.resolve()),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            removed = subprocess.run(
                ["docker", "rm", "-f", seed_container_id],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if removed.returncode != 0:
                raise RuntimeError("failed to remove workspace seed container")
    command = [
        "docker",
        "run",
        "--detach",
        "--name",
        spec.name,
        "--network",
        spec.network,
        "--cpus",
        spec.cpus,
        "--memory",
        spec.memory,
        "--workdir",
        spec.working_directory,
    ]
    if spec.user is not None:
        command.extend(("--user", spec.user))
    for name in spec.environment:
        command.extend(("-e", name))
    for source, destination, mode in spec.mounts:
        command.extend(("-v", f"{source.resolve()}:{destination}:{mode}"))
    command.extend((spec.image, "tail", "-f", "/dev/null"))
    started = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return RunningContainer(
        container_id=started.stdout.strip(),
        name=spec.name,
        image_id=resolved_image_id,
    )


def exec_task(
    container: RunningContainer,
    argv: list[str] | tuple[str, ...],
    timeout_seconds: int,
) -> ExecResult:
    if not argv:
        raise ValueError("exec argv must not be empty")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    command = [
        "docker",
        "exec",
        container.container_id,
        "timeout",
        "--signal=KILL",
        f"{timeout_seconds}s",
        *argv,
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 10,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        timed_out = exit_code in {124, 137}
    except subprocess.TimeoutExpired as error:
        exit_code = None
        stdout = str(error.stdout or "")
        stderr = str(error.stderr or "")
        timed_out = True
    return ExecResult(
        argv=tuple(argv),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=round(time.monotonic() - started, 6),
        timed_out=timed_out,
    )


def copy_into_container(
    container: RunningContainer, source: str | Path, destination: str
) -> None:
    subprocess.run(
        ["docker", "cp", str(Path(source).resolve()), f"{container.container_id}:{destination}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def remove_container(container: RunningContainer) -> CleanupResult:
    completed = subprocess.run(
        ["docker", "rm", "-f", container.container_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    stderr = completed.stderr or ""
    if completed.returncode == 0:
        return CleanupResult(success=True, removed=True, stderr=stderr)
    if "No such container" in stderr:
        return CleanupResult(success=True, removed=False, stderr=stderr)
    return CleanupResult(success=False, removed=False, stderr=stderr)
