"""Shared, mechanical pre-agent gate for generated candidates.

All commands execute in a fresh container of the candidate image.  The host only
constructs fixed path/tool checks; model-authored probe commands never run in the
host shell.
"""

from __future__ import annotations

import copy
import pathlib
import re
import shlex
import subprocess
from collections.abc import Callable
from typing import Any


_SPEC_FIELDS = {"required_paths", "required_tools", "task_probe", "unsolved_check"}
_PROBE_FIELDS = {"command", "allowed_exit_codes"}
_TOOL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]*\Z")


class CandidateSanityRejection(ValueError):
    """The model-authored candidate/sanity contract is invalid."""


class SanityInfrastructureError(RuntimeError):
    """The sanity gate could not run because Docker/host infrastructure failed."""


def _command(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise CandidateSanityRejection(
            f"{field} must be a nonempty shell command without NUL bytes"
        )
    try:
        checked = subprocess.run(
            ["/bin/bash", "-n", "-c", value],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SanityInfrastructureError(f"host bash syntax check failed: {error}") from error
    if checked.returncode != 0:
        raise CandidateSanityRejection(
            f"{field} has invalid shell syntax: {checked.stderr[-200:]}"
        )
    return value


def validate_sanity_spec(spec: Any) -> dict[str, Any]:
    """Return an isolated validated copy of the inspectable sanity contract."""
    if not isinstance(spec, dict):
        raise CandidateSanityRejection("candidate sanity must be an object")
    unknown = sorted(set(spec) - _SPEC_FIELDS)
    missing = sorted(_SPEC_FIELDS - set(spec))
    if unknown:
        raise CandidateSanityRejection(f"unknown sanity field: {unknown[0]}")
    if missing:
        raise CandidateSanityRejection(f"missing sanity field: {missing[0]}")

    paths = spec["required_paths"]
    if not isinstance(paths, list) or not paths:
        raise CandidateSanityRejection("required_paths must be a nonempty list")
    for value in paths:
        if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
            raise CandidateSanityRejection(
                "required_paths entries must be safe nonempty strings"
            )
        path = pathlib.PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts:
            raise CandidateSanityRejection(
                "required_paths entries must be absolute without '..'"
            )

    tools = spec["required_tools"]
    if not isinstance(tools, list) or not tools:
        raise CandidateSanityRejection("required_tools must be a nonempty list")
    if not all(isinstance(tool, str) and _TOOL_RE.fullmatch(tool) for tool in tools):
        raise CandidateSanityRejection(
            "required_tools entries must be command identifiers"
        )

    probe = spec["task_probe"]
    if not isinstance(probe, dict) or set(probe) != _PROBE_FIELDS:
        raise CandidateSanityRejection(
            "task_probe must contain command and allowed_exit_codes"
        )
    _command(probe["command"], "task_probe.command")
    allowed = probe["allowed_exit_codes"]
    if (
        not isinstance(allowed, list)
        or not allowed
        or any(isinstance(code, bool) or not isinstance(code, int) or not 0 <= code <= 255
               for code in allowed)
        or 125 in allowed
        or len(set(allowed)) != len(allowed)
    ):
        raise CandidateSanityRejection(
            "task_probe.allowed_exit_codes must be unique integers from 0 to 255 except 125"
        )

    unsolved = spec["unsolved_check"]
    if unsolved is not None:
        _command(unsolved, "unsolved_check")
    return copy.deepcopy(spec)


def _validate_image(image: Any) -> str:
    if (
        not isinstance(image, str)
        or not image
        or len(image) > 512
        or any(char.isspace() or char == "\x00" for char in image)
    ):
        raise CandidateSanityRejection(
            "candidate image must be a safe nonempty Docker reference"
        )
    return image


def docker_execute(image: str, command: str) -> tuple[int, str]:
    """Execute a probe only inside an unprivileged, network-isolated candidate."""
    image = _validate_image(image)
    try:
        process = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--entrypoint",
                "bash",
                image,
                "-lc",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as error:
        raise SanityInfrastructureError("candidate sanity Docker command timed out") from error
    except OSError as error:
        raise SanityInfrastructureError(f"candidate sanity Docker command failed: {error}") from error
    return process.returncode, (process.stdout + process.stderr)[-2000:]


def run_candidate_sanity(
    image: str,
    spec: Any,
    *,
    execute: Callable[[str, str], tuple[int, str]] | None = None,
) -> dict[str, Any]:
    """Run the exact shared path/tool/invocation/unsolved gate in fixed order."""
    image = _validate_image(image)
    validated = validate_sanity_spec(spec)
    execute = execute or docker_execute
    path_command = " && ".join(
        f"test -e {shlex.quote(path)}" for path in validated["required_paths"]
    )
    tool_command = " && ".join(
        f"command -v {shlex.quote(tool)} >/dev/null"
        for tool in validated["required_tools"]
    )
    planned = [
        ("required-paths", path_command, [0]),
        ("required-tools", tool_command, [0]),
        (
            "task-probe",
            validated["task_probe"]["command"],
            sorted(validated["task_probe"]["allowed_exit_codes"]),
        ),
    ]
    checks: list[dict[str, Any]] = []
    for name, command, allowed in planned:
        returncode, output = execute(image, command)
        if returncode == 125:
            raise SanityInfrastructureError(
                f"Docker could not execute sanity check {name}: {str(output)[-300:]}"
            )
        check = {
            "name": name,
            "command": command,
            "returncode": returncode,
            "allowed_exit_codes": allowed,
            "output_tail": str(output)[-500:],
        }
        checks.append(check)
        if returncode not in allowed:
            return {
                "schema": "candidate-sanity/1",
                "image": image,
                "valid": False,
                "rejection": name,
                "checks": checks,
            }

    unsolved = validated["unsolved_check"]
    if unsolved is None:
        checks.append({"name": "unsolved", "status": "not-configured"})
    else:
        returncode, output = execute(image, unsolved)
        if returncode == 125:
            raise SanityInfrastructureError(
                f"Docker could not execute sanity check unsolved: {str(output)[-300:]}"
            )
        checks.append(
            {
                "name": "unsolved",
                "command": unsolved,
                "returncode": returncode,
                "allowed_exit_codes": [0],
                "output_tail": str(output)[-500:],
            }
        )
        if returncode != 0:
            return {
                "schema": "candidate-sanity/1",
                "image": image,
                "valid": False,
                "rejection": "unsolved",
                "checks": checks,
            }
    return {
        "schema": "candidate-sanity/1",
        "image": image,
        "valid": True,
        "rejection": None,
        "checks": checks,
    }
