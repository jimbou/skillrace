"""Fail-closed OS confinement for RQ3 public model-execution phases.

Each child is deliberately given an empty mount namespace.  Only the Python
runtime, SkillRACE package, staged public inputs, phase output, and (when
requested) the Docker Unix socket are mounted back into it.  The provider API
requires retaining the host network namespace; filesystem isolation does not.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from .io_utils import canonical_json_hash, file_hash


SCHEMA = "skillrace-rq3-public-confinement/1"
DEFAULT_BWRAP = pathlib.Path("/usr/bin/bwrap")
_PASSTHROUGH_ENVIRONMENT = (
    "CLOSE_API_KEY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "DOCKER_HOST",
    "DOCKER_CONFIG",
)
_READ_ONLY_PURPOSES = {
    "system-runtime",
    "network-config",
    "python-runtime",
    "python-package",
    "public-input",
    "docker-config",
    "certificate-override",
}


class Phase1IsolationError(RuntimeError):
    """A public model-execution phase cannot use its frozen confinement."""


@dataclasses.dataclass(frozen=True)
class Phase1Confinement:
    """Executable launch material plus its secret-free artifact record."""

    command: tuple[str, ...]
    environment: dict[str, str]
    record: dict[str, Any]


def _bubblewrap_command(
    *,
    engine: str,
    mounts: Sequence[Mapping[str, str]],
    cwd: str,
    inner_argv: Sequence[str],
) -> list[str]:
    command = [
        engine,
        "--die-with-parent",
        "--unshare-all",
        "--share-net",
        "--new-session",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/tmp/skillrace-home",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--symlink",
        "usr/sbin",
        "/sbin",
    ]
    for mount in mounts:
        option = "--ro-bind" if mount.get("mode") == "ro" else "--bind"
        command.extend((option, str(mount.get("source")), str(mount.get("target"))))
    command.extend(("--chdir", cwd, "--", *inner_argv))
    return command


def _resolved_existing(path: str | pathlib.Path, label: str, *, directory: bool) -> pathlib.Path:
    raw = pathlib.Path(path)
    if raw.is_symlink() and label in {"public input", "campaign output"}:
        raise Phase1IsolationError(f"{label} symlink is forbidden: {raw}")
    resolved = raw.resolve()
    valid = resolved.is_dir() if directory else resolved.exists()
    if not valid:
        kind = "directory" if directory else "path"
        raise Phase1IsolationError(f"{label} {kind} is missing: {resolved}")
    return resolved


def _is_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    return path == root or root in path.parents


def _mount(
    mounts: list[dict[str, str]],
    *,
    source: pathlib.Path,
    target: pathlib.Path,
    mode: str,
    purpose: str,
    kind: str = "path",
) -> None:
    entry = {
        "source": str(source),
        "target": str(target),
        "mode": mode,
        "purpose": purpose,
        "kind": kind,
    }
    if entry not in mounts:
        mounts.append(entry)


def _docker_mount(
    environment: Mapping[str, str], mounts: list[dict[str, str]]
) -> dict[str, Any]:
    host = environment.get("DOCKER_HOST") or "unix:///var/run/docker.sock"
    if not host.startswith("unix://"):
        raise Phase1IsolationError(
            "production RQ3 permits only a Unix Docker socket"
        )
    target = pathlib.Path(host.removeprefix("unix://"))
    if not target.is_absolute():
        raise Phase1IsolationError("DOCKER_HOST Unix socket must be absolute")
    source = target.resolve()
    try:
        mode = source.stat().st_mode
    except OSError as error:
        raise Phase1IsolationError(f"Docker socket is unavailable: {source}") from error
    if not stat.S_ISSOCK(mode):
        raise Phase1IsolationError(f"Docker endpoint is not a Unix socket: {source}")
    _mount(
        mounts,
        source=source,
        target=target,
        mode="rw",
        purpose="docker-socket",
        kind="socket",
    )
    return {
        "authority": "host-daemon-socket",
        "endpoint_scheme": "unix",
        "socket_mounted": True,
        "limitation": (
            "the trusted campaign process can ask the host Docker daemon to access "
            "host paths outside this mount namespace; generated agents do not receive "
            "the socket"
        ),
    }


def _clean_environment(
    environ: Mapping[str, str],
    *,
    python_prefix: pathlib.Path,
    package_parent: pathlib.Path,
    ledger_path: pathlib.Path,
) -> dict[str, str]:
    clean = {
        "PATH": f"{python_prefix / 'bin'}:/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp/skillrace-home",
        "TMPDIR": "/tmp",
        "PYTHONPATH": str(package_parent),
        "SKILLRACE_LEDGER": str(ledger_path),
        "LANG": environ.get("LANG", "C.UTF-8"),
        "LC_ALL": environ.get("LC_ALL", "C.UTF-8"),
    }
    for name in _PASSTHROUGH_ENVIRONMENT:
        value = environ.get(name)
        if value is not None:
            if not isinstance(value, str) or "\x00" in value:
                raise Phase1IsolationError(f"unsafe environment value for {name}")
            clean[name] = value
    return clean


def _engine_version(path: pathlib.Path) -> str:
    process = subprocess.run(
        [str(path), "--version"],
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode:
        raise Phase1IsolationError("bubblewrap version probe failed")
    version = (process.stdout or process.stderr).strip()
    if not version:
        raise Phase1IsolationError("bubblewrap returned an empty version")
    return version


def build_phase1_confinement(
    *,
    inner_argv: Sequence[str],
    cwd: str | pathlib.Path,
    public_roots: Sequence[str | pathlib.Path],
    output_root: str | pathlib.Path,
    ledger_path: str | pathlib.Path,
    environ: Mapping[str, str] | None = None,
    require_docker: bool = True,
    bwrap_path: str | pathlib.Path = DEFAULT_BWRAP,
    role: str = "campaign",
) -> Phase1Confinement:
    """Build one deterministic, auditable bubblewrap launch.

    All filesystem paths retain their host absolute names inside the namespace so
    existing campaign code does not need path translation.  The namespace starts
    empty, so retaining an absolute name does not make an unmounted path visible.
    """

    if role not in {"campaign", "confirmation", "revision"}:
        raise Phase1IsolationError(f"unknown public confinement role: {role!r}")
    if not inner_argv or any(not isinstance(item, str) or "\x00" in item for item in inner_argv):
        raise Phase1IsolationError("inner command must contain safe non-empty arguments")
    engine = pathlib.Path(bwrap_path)
    if engine.is_symlink():
        engine = engine.resolve()
    if not engine.is_file() or not os.access(engine, os.X_OK):
        raise Phase1IsolationError(f"bubblewrap executable is unavailable: {engine}")
    engine = engine.resolve()

    workdir = _resolved_existing(cwd, "campaign cwd", directory=True)
    public = tuple(
        _resolved_existing(path, "public input", directory=True)
        for path in public_roots
    )
    if not public:
        raise Phase1IsolationError("at least one public input root is required")
    output = _resolved_existing(output_root, "campaign output", directory=True)
    if any(_is_within(root, output) or _is_within(output, root) for root in public):
        raise Phase1IsolationError("public inputs and writable output must not overlap")
    if not any(_is_within(workdir, root) for root in public):
        raise Phase1IsolationError("campaign cwd must be inside a public input root")
    ledger = pathlib.Path(ledger_path).resolve()
    if not _is_within(ledger, output):
        raise Phase1IsolationError("provider ledger must be inside campaign output")
    ledger.parent.mkdir(parents=True, exist_ok=True)

    package = pathlib.Path(__file__).resolve().parent
    package_parent = package.parent
    python_prefix = pathlib.Path(sys.prefix).resolve()
    source_environment = dict(os.environ if environ is None else environ)
    clean_environment = _clean_environment(
        source_environment,
        python_prefix=python_prefix,
        package_parent=package_parent,
        ledger_path=ledger,
    )
    if not require_docker:
        clean_environment.pop("DOCKER_HOST", None)
        clean_environment.pop("DOCKER_CONFIG", None)

    mounts: list[dict[str, str]] = []
    _mount(
        mounts,
        source=pathlib.Path("/usr"),
        target=pathlib.Path("/usr"),
        mode="ro",
        purpose="system-runtime",
        kind="directory",
    )
    for source, target in (
        (pathlib.Path("/etc/hosts"), pathlib.Path("/etc/hosts")),
        (pathlib.Path("/etc/nsswitch.conf"), pathlib.Path("/etc/nsswitch.conf")),
        (pathlib.Path("/etc/resolv.conf").resolve(), pathlib.Path("/etc/resolv.conf")),
        (pathlib.Path("/etc/ssl/certs"), pathlib.Path("/etc/ssl/certs")),
        (pathlib.Path("/etc/ssl/openssl.cnf"), pathlib.Path("/etc/ssl/openssl.cnf")),
    ):
        if source.exists():
            _mount(
                mounts,
                source=source,
                target=target,
                mode="ro",
                purpose="network-config",
                kind="directory" if source.is_dir() else "file",
            )
    if not _is_within(python_prefix, pathlib.Path("/usr")):
        _mount(
            mounts,
            source=python_prefix,
            target=python_prefix,
            mode="ro",
            purpose="python-runtime",
            kind="directory",
        )
    _mount(
        mounts,
        source=package,
        target=package,
        mode="ro",
        purpose="python-package",
        kind="directory",
    )
    for root in public:
        _mount(
            mounts,
            source=root,
            target=root,
            mode="ro",
            purpose="public-input",
            kind="directory",
        )
    _mount(
        mounts,
        source=output,
        target=output,
        mode="rw",
        purpose=f"{role}-output-and-ledger",
        kind="directory",
    )

    for variable in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        configured = clean_environment.get(variable)
        if configured:
            certificate = _resolved_existing(
                configured, f"{variable} certificate", directory=False
            )
            trusted_certificate_roots = (
                pathlib.Path("/etc/ssl"),
                pathlib.Path("/etc/pki"),
                pathlib.Path("/usr/share/ca-certificates"),
                pathlib.Path("/usr/local/share/ca-certificates"),
                python_prefix,
                *public,
            )
            if not any(
                root.exists() and _is_within(certificate, root.resolve())
                for root in trusted_certificate_roots
            ):
                raise Phase1IsolationError(
                    f"{variable} certificate is outside the trusted runtime/public inputs"
                )
            if not any(_is_within(certificate, pathlib.Path(item["source"])) for item in mounts):
                _mount(
                    mounts,
                    source=certificate,
                    target=pathlib.Path(configured),
                    mode="ro",
                    purpose="certificate-override",
                    kind="file",
                )
    docker_config = clean_environment.get("DOCKER_CONFIG")
    if docker_config:
        configured_path = pathlib.Path(docker_config).expanduser()
        host_home = pathlib.Path(
            source_environment.get("HOME", str(pathlib.Path.home()))
        ).expanduser()
        trusted_docker_root = host_home / ".docker"
        lexical_config = pathlib.Path(os.path.abspath(configured_path))
        lexical_trusted = pathlib.Path(os.path.abspath(trusted_docker_root))
        if (
            not configured_path.is_absolute()
            or configured_path.is_symlink()
            or trusted_docker_root.is_symlink()
            or not _is_within(lexical_config, lexical_trusted)
        ):
            raise Phase1IsolationError(
                "Docker config must be a regular directory inside the trusted host ~/.docker"
            )
        config = _resolved_existing(configured_path, "Docker config", directory=True)
        if not _is_within(config, lexical_trusted.resolve()):
            raise Phase1IsolationError(
                "Docker config resolves outside the trusted host ~/.docker"
            )
        _mount(
            mounts,
            source=config,
            target=pathlib.Path(docker_config),
            mode="ro",
            purpose="docker-config",
            kind="directory",
        )
    docker_boundary: dict[str, Any]
    if require_docker:
        docker_boundary = _docker_mount(clean_environment, mounts)
    else:
        docker_boundary = {
            "authority": "not-mounted-not-required",
            "socket_mounted": False,
        }

    command = _bubblewrap_command(
        engine=str(engine),
        mounts=mounts,
        cwd=str(workdir),
        inner_argv=inner_argv,
    )

    core: dict[str, Any] = {
        "schema": SCHEMA,
        "enforced": True,
        "role": role,
        "engine": {
            "path": str(engine),
            "sha256": file_hash(engine),
            "version": _engine_version(engine),
        },
        "command": command,
        "inner_argv": list(inner_argv),
        "cwd": str(workdir),
        "namespaces": {
            "unshare_all": True,
            "new_session": True,
            "die_with_parent": True,
        },
        "network": {
            "mode": "host-shared",
            "reason": (
                "CloseAI HTTPS and Docker endpoint access are required"
                if require_docker
                else "CloseAI HTTPS access is required"
            ),
        },
        "filesystem": {
            "host_root": "absent",
            "unlisted_paths": "absent",
            "temporary_storage": "private-tmpfs",
            "devices": "private-minimal-dev",
            "mounts": mounts,
            "ledger_path": str(ledger),
        },
        "environment": {
            "mode": "exact-subprocess-allowlist",
            "names": sorted(clean_environment),
            "secret_names": sorted(
                name for name in clean_environment if "KEY" in name or "TOKEN" in name
            ),
            "values_recorded": False,
        },
        "trust_boundaries": {"docker": docker_boundary},
    }
    record = {**core, "policy_hash": canonical_json_hash(core)}
    validate_phase1_confinement_record(record)
    return Phase1Confinement(tuple(command), clean_environment, record)


def validate_phase1_confinement_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a saved confinement record independently of campaign state."""

    if not isinstance(record, Mapping):
        raise Phase1IsolationError("confinement record must be an object")
    value = dict(record)
    supplied_hash = value.pop("policy_hash", None)
    if supplied_hash != canonical_json_hash(value):
        raise Phase1IsolationError("confinement policy hash mismatch")
    if value.get("schema") != SCHEMA or value.get("enforced") is not True:
        raise Phase1IsolationError("production confinement schema/enforcement mismatch")
    role = value.get("role")
    if role not in {"campaign", "confirmation", "revision"}:
        raise Phase1IsolationError("production confinement role mismatch")
    filesystem = value.get("filesystem")
    if not isinstance(filesystem, Mapping):
        raise Phase1IsolationError("confinement filesystem policy is malformed")
    if set(filesystem) != {
        "host_root",
        "unlisted_paths",
        "temporary_storage",
        "devices",
        "mounts",
        "ledger_path",
    }:
        raise Phase1IsolationError("confinement filesystem fields mismatch")
    if filesystem.get("host_root") != "absent" or filesystem.get("unlisted_paths") != "absent":
        raise Phase1IsolationError("confinement must start from an empty host root")
    if (
        filesystem.get("temporary_storage") != "private-tmpfs"
        or filesystem.get("devices") != "private-minimal-dev"
    ):
        raise Phase1IsolationError("confinement private tmp/dev policy mismatch")
    if value.get("namespaces") != {
        "unshare_all": True,
        "new_session": True,
        "die_with_parent": True,
    }:
        raise Phase1IsolationError("confinement namespace policy mismatch")
    network = value.get("network")
    if (
        not isinstance(network, Mapping)
        or network.get("mode") != "host-shared"
        or network.get("reason")
        not in {
            "CloseAI HTTPS and Docker endpoint access are required",
            "CloseAI HTTPS access is required",
        }
    ):
        raise Phase1IsolationError("confinement network policy mismatch")
    command = value.get("command")
    inner = value.get("inner_argv")
    engine = value.get("engine")
    if not isinstance(command, list) or not command or not isinstance(inner, list):
        raise Phase1IsolationError("confinement command is malformed")
    if not isinstance(engine, Mapping) or command[0] != engine.get("path"):
        raise Phase1IsolationError("confinement engine identity mismatch")
    engine_path = pathlib.Path(str(engine.get("path", "")))
    if not engine_path.is_file() or file_hash(engine_path) != engine.get("sha256"):
        raise Phase1IsolationError("confinement engine binary hash mismatch")
    if set(engine) != {"path", "sha256", "version"} or engine.get(
        "version"
    ) != _engine_version(engine_path):
        raise Phase1IsolationError("confinement engine version mismatch")
    required_flags = {"--die-with-parent", "--unshare-all", "--share-net", "--new-session"}
    if not required_flags.issubset(command):
        raise Phase1IsolationError("confinement namespace flags are incomplete")
    try:
        separator = command.index("--")
    except ValueError as error:
        raise Phase1IsolationError("confinement command separator is missing") from error
    if command[separator + 1 :] != inner:
        raise Phase1IsolationError("confinement inner command mismatch")
    mounts = filesystem.get("mounts")
    if not isinstance(mounts, list) or not mounts:
        raise Phase1IsolationError("confinement mount policy is empty")
    recorded_command_mounts = [
        ["--ro-bind" if mount.get("mode") == "ro" else "--bind", mount.get("source"), mount.get("target")]
        for mount in mounts
        if isinstance(mount, Mapping)
    ]
    actual_command_mounts = [
        command[index : index + 3]
        for index in range(1, separator)
        if command[index] in {"--ro-bind", "--bind"}
    ]
    if actual_command_mounts != recorded_command_mounts:
        raise Phase1IsolationError(
            "confinement command contains an unrecorded or reordered mount"
        )
    cwd = value.get("cwd")
    if not isinstance(cwd, str) or not pathlib.Path(cwd).is_absolute():
        raise Phase1IsolationError("confinement cwd is malformed")
    expected_command = _bubblewrap_command(
        engine=str(engine["path"]), mounts=mounts, cwd=cwd, inner_argv=inner
    )
    if command != expected_command:
        raise Phase1IsolationError("confinement differs from the exact frozen command")
    writable_directories = 0
    for mount in mounts:
        if not isinstance(mount, Mapping):
            raise Phase1IsolationError("confinement mount entry is malformed")
        if set(mount) != {"source", "target", "mode", "purpose", "kind"}:
            raise Phase1IsolationError("confinement mount fields mismatch")
        source = mount.get("source")
        target = mount.get("target")
        mode = mount.get("mode")
        purpose = mount.get("purpose")
        kind = mount.get("kind")
        if not all(isinstance(item, str) and item for item in (source, target, mode, purpose, kind)):
            raise Phase1IsolationError("confinement mount entry fields are malformed")
        if source == "/" or target == "/":
            raise Phase1IsolationError("binding the host root is forbidden")
        if purpose in _READ_ONLY_PURPOSES and mode != "ro":
            raise Phase1IsolationError(f"{purpose} must be read-only")
        if mode == "rw" and kind == "directory":
            writable_directories += 1
            if purpose != f"{role}-output-and-ledger":
                raise Phase1IsolationError("unexpected writable directory mount")
        option = "--ro-bind" if mode == "ro" else "--bind" if mode == "rw" else None
        if option is None:
            raise Phase1IsolationError("unknown confinement mount mode")
        triplet = [option, source, target]
        if not any(command[index : index + 3] == triplet for index in range(separator - 2)):
            raise Phase1IsolationError("recorded mount is absent from confinement command")
    if writable_directories != 1:
        raise Phase1IsolationError("exactly one writable campaign output is required")
    output_mount = next(
        mount
        for mount in mounts
        if mount.get("mode") == "rw" and mount.get("kind") == "directory"
    )
    ledger_path = filesystem.get("ledger_path")
    if (
        not isinstance(ledger_path, str)
        or not pathlib.Path(ledger_path).is_absolute()
        or not _is_within(
            pathlib.Path(ledger_path), pathlib.Path(output_mount["target"])
        )
    ):
        raise Phase1IsolationError("confinement ledger escapes writable output")
    environment = value.get("environment")
    if (
        not isinstance(environment, Mapping)
        or set(environment) != {
            "mode",
            "names",
            "secret_names",
            "values_recorded",
        }
        or environment.get("mode") != "exact-subprocess-allowlist"
        or environment.get("values_recorded") is not False
        or not isinstance(environment.get("names"), list)
        or any(not isinstance(name, str) for name in environment.get("names", []))
        or environment["names"] != sorted(set(environment["names"]))
        or environment.get("secret_names")
        != sorted(
            name
            for name in environment["names"]
            if "KEY" in name or "TOKEN" in name
        )
    ):
        raise Phase1IsolationError("confinement environment record is malformed")
    trust = value.get("trust_boundaries")
    docker = trust.get("docker") if isinstance(trust, Mapping) else None
    if not isinstance(docker, Mapping) or set(trust) != {"docker"}:
        raise Phase1IsolationError("confinement Docker trust boundary is malformed")
    authority = docker.get("authority")
    socket_mounts = [
        mount for mount in mounts if mount.get("purpose") == "docker-socket"
    ]
    if authority == "not-mounted-not-required":
        if docker != {
            "authority": "not-mounted-not-required",
            "socket_mounted": False,
        } or socket_mounts:
            raise Phase1IsolationError("unneeded Docker authority is exposed")
        expected_network_reason = "CloseAI HTTPS access is required"
    elif authority == "host-daemon-socket":
        if (
            docker.get("endpoint_scheme") != "unix"
            or docker.get("socket_mounted") is not True
            or not isinstance(docker.get("limitation"), str)
            or len(socket_mounts) != 1
            or socket_mounts[0].get("mode") != "rw"
            or socket_mounts[0].get("kind") != "socket"
        ):
            raise Phase1IsolationError("Docker socket trust boundary mismatch")
        expected_network_reason = (
            "CloseAI HTTPS and Docker endpoint access are required"
        )
    else:
        raise Phase1IsolationError("unknown Docker trust boundary")
    if network.get("reason") != expected_network_reason:
        raise Phase1IsolationError("network/Docker trust boundary mismatch")
    return dict(record)
