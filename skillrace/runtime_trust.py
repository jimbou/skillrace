"""Host-measured integrity checks for the trusted agent runtime in Docker images."""

from __future__ import annotations

import hashlib
import os
import pathlib
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass


PROTECTED_RUNTIME_PATHS = (
    "/usr/local/bin/pi",
    "/usr/local/lib/node_modules/@mariozechner/pi-coding-agent",
    "/root/.pi",
    "/usr/local/bin/node",
    "/bin/bash",
    "/bin/sh",
    "/usr/bin/env",
    "/usr/bin/git",
    "/usr/bin/sleep",
    "/usr/lib/git-core",
    "/usr/lib/x86_64-linux-gnu/gconv",
    "/usr/lib/x86_64-linux-gnu/ossl-modules",
    "/usr/lib/aarch64-linux-gnu/gconv",
    "/usr/lib/aarch64-linux-gnu/ossl-modules",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/profile",
    "/etc/profile.d",
    "/etc/bash.bashrc",
    "/etc/environment",
    "/etc/ld.so.preload",
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/lib64/ld-linux-x86-64.so.2",
    "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
    "/lib/x86_64-linux-gnu/libc.so.6",
    "/lib/x86_64-linux-gnu/libdl.so.2",
    "/lib/x86_64-linux-gnu/libm.so.6",
    "/lib/x86_64-linux-gnu/libpthread.so.0",
    "/lib/x86_64-linux-gnu/libtinfo.so.6",
    "/lib/x86_64-linux-gnu/libgcc_s.so.1",
    "/usr/lib/x86_64-linux-gnu/libstdc++.so.6",
    "/lib/ld-linux-aarch64.so.1",
    "/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1",
    "/lib/aarch64-linux-gnu/libc.so.6",
    "/lib/aarch64-linux-gnu/libdl.so.2",
    "/lib/aarch64-linux-gnu/libm.so.6",
    "/lib/aarch64-linux-gnu/libpthread.so.0",
    "/lib/aarch64-linux-gnu/libtinfo.so.6",
    "/lib/aarch64-linux-gnu/libgcc_s.so.1",
    "/usr/lib/aarch64-linux-gnu/libstdc++.so.6",
    "/root/.bash_profile",
    "/root/.bashrc",
    "/root/.profile",
    "/root/.gitconfig",
)
REQUIRED_RUNTIME_PATHS = frozenset(
    {
        "/usr/local/bin/pi",
        "/usr/local/lib/node_modules/@mariozechner/pi-coding-agent",
        "/root/.pi",
        "/usr/local/bin/node",
        "/bin/bash",
        "/bin/sh",
        "/usr/bin/env",
        "/usr/bin/git",
        "/usr/bin/sleep",
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/ld.so.cache",
        "/etc/ld.so.conf",
        "/etc/ld.so.conf.d",
    }
)
REQUIRED_LOADER_ALTERNATIVES = frozenset(
    {
        "/lib64/ld-linux-x86-64.so.2",
        "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
        "/lib/ld-linux-aarch64.so.1",
        "/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1",
    }
)
MANDATORY_DEPENDENCY_EXECUTABLES = (
    "/bin/bash",
    "/bin/sh",
    "/usr/local/bin/node",
    "/usr/local/bin/pi",
    "/usr/bin/git",
    "/usr/bin/env",
    "/usr/bin/sleep",
)
OPTIONAL_GIT_HELPERS = (
    "/usr/lib/git-core/git",
    "/usr/lib/git-core/git-http-fetch",
    "/usr/lib/git-core/git-http-push",
    "/usr/lib/git-core/git-receive-pack",
    "/usr/lib/git-core/git-remote-http",
    "/usr/lib/git-core/git-remote-https",
    "/usr/lib/git-core/git-upload-pack",
)
NON_DYNAMIC_EXECUTABLES = frozenset({"/usr/local/bin/pi"})


class RuntimeFingerprintError(RuntimeError):
    """Docker/host infrastructure prevented a trustworthy measurement."""


class RuntimeIntegrityError(RuntimeError):
    """A candidate changed or removed protected runtime content."""


class DependencyDiscoveryError(RuntimeFingerprintError):
    """Trusted-base dynamic dependency discovery was incomplete or malformed."""


@dataclass(frozen=True)
class BaseTrustRecord:
    fingerprint: dict[str, str]
    dependency_paths: tuple[str, ...]


_BASE_FINGERPRINT_CACHE: dict[str, BaseTrustRecord] = {}


def _hash_host_path(path: pathlib.Path) -> str:
    digest = hashlib.sha256()

    def visit(current: pathlib.Path, relative: str) -> None:
        info = current.lstat()
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(stat.S_IMODE(info.st_mode)).encode())
        digest.update(b"\0")
        if current.is_symlink():
            digest.update(b"L\0" + os.readlink(current).encode())
        elif current.is_file():
            digest.update(b"F\0")
            with current.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        elif current.is_dir():
            digest.update(b"D\0")
            for child in sorted(current.iterdir(), key=lambda item: item.name):
                child_relative = f"{relative}/{child.name}" if relative else child.name
                visit(child, child_relative)
        else:
            digest.update(b"O\0")

    visit(path, "")
    return digest.hexdigest()


def _completed(run, argv):
    try:
        return run(argv, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeFingerprintError(f"Docker fingerprint command failed: {error}") from error


def fingerprint_image(
    image: str,
    *,
    protected_paths: Iterable[str] = PROTECTED_RUNTIME_PATHS,
    run: Callable = subprocess.run,
    temp_root: str | pathlib.Path | None = None,
) -> dict[str, str]:
    """Copy protected paths to the host and hash them without image-side tools."""
    created = _completed(
        run, ["docker", "create", "--entrypoint", "/bin/true", image]
    )
    if created.returncode != 0 or not created.stdout.strip():
        raise RuntimeFingerprintError(
            f"could not create fingerprint container: {created.stderr[-300:]}"
        )
    container = created.stdout.strip()
    fingerprint: dict[str, str] = {}
    try:
        with tempfile.TemporaryDirectory(dir=temp_root) as temporary:
            root = pathlib.Path(temporary)
            for index, protected in enumerate(protected_paths):
                destination = root / f"item-{index}"
                copied = _completed(
                    run,
                    [
                        "docker", "cp", "-L",
                        f"{container}:{protected}", str(destination),
                    ],
                )
                if copied.returncode == 0:
                    if not destination.exists() and not destination.is_symlink():
                        raise RuntimeFingerprintError(
                            f"docker cp reported success without {protected}"
                        )
                    fingerprint[protected] = _hash_host_path(destination)
                    continue
                message = (copied.stderr + copied.stdout).lower()
                if "no such file" in message or "could not find" in message:
                    fingerprint[protected] = "<missing>"
                    continue
                raise RuntimeFingerprintError(
                    f"could not copy protected path {protected}: {message[-300:]}"
                )
    finally:
        removed = _completed(run, ["docker", "rm", "-f", container])
        if removed.returncode != 0:
            raise RuntimeFingerprintError(
                f"could not remove fingerprint container {container}"
            )
    return fingerprint


def parse_ldd_dependencies(output: str) -> tuple[str, ...]:
    """Parse all absolute dependency/loader paths emitted by trusted-base ldd."""
    dependencies: set[str] = set()
    saw_content = False
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        saw_content = True
        lowered = line.lower()
        if line.startswith("linux-vdso") or lowered in {
            "statically linked",
            "not a dynamic executable",
        }:
            continue
        if "=>" in line:
            _, right = line.split("=>", 1)
            right = right.strip()
            if right.startswith("not found"):
                raise DependencyDiscoveryError(
                    f"trusted dependency is missing: {line}"
                )
            resolved = right.split(None, 1)[0] if right else ""
            if not resolved.startswith("/"):
                raise DependencyDiscoveryError(
                    f"ldd dependency is not an absolute resolved path: {line}"
                )
            dependencies.add(resolved)
            continue
        if line.startswith("/"):
            dependencies.add(line.split(None, 1)[0])
            continue
        if lowered.startswith("ldd: warning:"):
            continue
        raise DependencyDiscoveryError(f"malformed ldd output: {line}")
    if not saw_content:
        raise DependencyDiscoveryError("ldd produced no dependency information")
    return tuple(sorted(dependencies))


def _docker_ldd_execute(image: str, executable: str, *, run=subprocess.run):
    try:
        result = run(
            [
                "docker", "run", "--rm", "--network=none",
                "--entrypoint", "/usr/bin/ldd", image, executable,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise DependencyDiscoveryError(
            f"trusted-base ldd failed for {executable}: {error}"
        ) from error
    return result.returncode, result.stdout + result.stderr


def discover_base_dependencies(
    base_image: str,
    *,
    execute: Callable[[str, str], tuple[int, str]] | None = None,
    mandatory_executables: Iterable[str] = MANDATORY_DEPENDENCY_EXECUTABLES,
    optional_executables: Iterable[str] = OPTIONAL_GIT_HELPERS,
) -> tuple[str, ...]:
    """Discover dependencies only by executing ldd in the trusted declared base."""
    execute = execute or _docker_ldd_execute
    dependencies: set[str] = set()
    for executable, mandatory in [
        *((path, True) for path in mandatory_executables),
        *((path, False) for path in optional_executables),
    ]:
        try:
            returncode, output = execute(base_image, executable)
        except DependencyDiscoveryError:
            raise
        except Exception as error:
            raise DependencyDiscoveryError(
                f"trusted-base dependency discovery failed for {executable}: {error}"
            ) from error
        lowered = output.lower()
        if "not a dynamic executable" in lowered and executable in NON_DYNAMIC_EXECUTABLES:
            continue
        if returncode != 0:
            if not mandatory and (
                "no such file" in lowered or "not found" in lowered
            ):
                continue
            raise DependencyDiscoveryError(
                f"trusted-base ldd failed for {executable}: {output[-300:]}"
            )
        dependencies.update(parse_ldd_dependencies(output))
    return tuple(sorted(dependencies))


def _fingerprint_dependency_paths(image: str, paths: Iterable[str]):
    return fingerprint_image(image, protected_paths=paths)


def compare_runtime_fingerprints(
    base: dict[str, str], candidate: dict[str, str]
) -> None:
    changed = [
        path
        for path in sorted(set(base) | set(candidate))
        if base.get(path) != candidate.get(path)
    ]
    if changed:
        raise RuntimeIntegrityError(
            "candidate changed protected runtime path(s): " + ", ".join(changed)
        )


def _validate_declared_base(fingerprint: dict[str, str]) -> None:
    """Fail before executing dependency discovery in an invalid declared base."""
    missing_base = sorted(
        path
        for path in REQUIRED_RUNTIME_PATHS
        if fingerprint.get(path) in {None, "<missing>"}
    )
    if missing_base:
        raise RuntimeFingerprintError(
            "declared base is missing required runtime path(s): "
            + ", ".join(missing_base)
        )
    if not any(
        fingerprint.get(path) not in {None, "<missing>"}
        for path in REQUIRED_LOADER_ALTERNATIVES
    ):
        raise RuntimeFingerprintError(
            "declared base is missing every supported platform dynamic loader"
        )


def verify_runtime_integrity(
    base_image: str,
    candidate_image: str,
    *,
    fingerprint: Callable[[str], dict[str, str]] = fingerprint_image,
    dependency_discover: Callable[[str], tuple[str, ...]] = discover_base_dependencies,
    dependency_fingerprint: Callable[[str, Iterable[str]], dict[str, str]] = (
        _fingerprint_dependency_paths
    ),
    base_cache: dict[str, BaseTrustRecord] | None = None,
) -> dict[str, str]:
    cache = _BASE_FINGERPRINT_CACHE if base_cache is None else base_cache
    if base_image not in cache:
        base_static = fingerprint(base_image)
        _validate_declared_base(base_static)
        dependency_paths = tuple(sorted(set(dependency_discover(base_image))))
        base_dynamic = dependency_fingerprint(base_image, dependency_paths)
        cache[base_image] = BaseTrustRecord(
            fingerprint={**base_static, **base_dynamic},
            dependency_paths=dependency_paths,
        )
    record = cache[base_image]
    _validate_declared_base(record.fingerprint)
    candidate = {
        **fingerprint(candidate_image),
        **dependency_fingerprint(candidate_image, record.dependency_paths),
    }
    compare_runtime_fingerprints(record.fingerprint, candidate)
    return candidate
