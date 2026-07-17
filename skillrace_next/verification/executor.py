import json
from pathlib import Path, PurePosixPath
import subprocess
import time
from typing import Any

from ..records import CheckBundle, CheckResults
from ..runtime.artifacts import freeze_artifact
from ..runtime.docker import (
    ExecResult,
    RunningContainer,
    copy_into_container,
    remove_container,
)
from ..storage import atomic_write_json, canonical_json_hash, tree_hash


_CHECKER_UID = 65534
_CHECK_ROOT = "/tmp/skillrace-checks"
_SCRATCH_ROOT = "/tmp/skillrace-check-work"
_MAX_STREAM_BYTES = 65_536


def _valid_evidence_paths(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, str) or not item:
            return False
        path = PurePosixPath(item)
        if path.is_absolute() or ".." in path.parts:
            return False
    return True


def interpret_checker_result(
    check: dict[str, Any],
    execution: ExecResult,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    result = {
        "check_id": check["check_id"],
        "property_id": check["property_id"],
        "status": "inconclusive",
        "exit_code": execution.exit_code,
        "duration_seconds": execution.duration_seconds,
        "diagnostic": "checker outcome was invalid",
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "evidence_paths": [],
    }
    if execution.timed_out:
        result["diagnostic"] = "checker timed out"
        return result
    try:
        payload = json.loads(execution.stdout)
    except (json.JSONDecodeError, TypeError):
        result["diagnostic"] = "checker stdout was not one JSON object"
        return result
    if not isinstance(payload, dict):
        result["diagnostic"] = "checker stdout JSON was not an object"
        return result
    diagnostic = payload.get("diagnostic")
    evidence_paths = payload.get("evidence_paths")
    if not isinstance(diagnostic, str) or not diagnostic.strip():
        result["diagnostic"] = "checker diagnostic was missing"
        return result
    if not _valid_evidence_paths(evidence_paths):
        result["diagnostic"] = "checker evidence_paths were invalid"
        return result
    status = {0: "pass", 1: "fail", 2: "inconclusive"}.get(execution.exit_code)
    if status is None:
        result["diagnostic"] = f"checker exited unexpectedly: {execution.exit_code}"
        return result
    result["status"] = status
    result["diagnostic"] = diagnostic.strip()
    result["evidence_paths"] = evidence_paths
    return result


def _bounded_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        encoded = value
    else:
        encoded = value.encode("utf-8", errors="replace")
    if len(encoded) > _MAX_STREAM_BYTES:
        encoded = encoded[:_MAX_STREAM_BYTES] + b"\n[output truncated]\n"
    return encoded.decode("utf-8", errors="replace")


def _docker_setup(container: RunningContainer, bundle: CheckBundle) -> None:
    for argv in (
        ["mkdir", "-p", f"{_CHECK_ROOT}/checks", _SCRATCH_ROOT],
        ["chown", f"{_CHECKER_UID}:{_CHECKER_UID}", _SCRATCH_ROOT],
        ["chmod", "700", _SCRATCH_ROOT],
    ):
        subprocess.run(
            ["docker", "exec", "--user", "0", container.container_id, *argv],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    copy_into_container(
        container,
        bundle.manifest_path,
        f"{_CHECK_ROOT}/check_manifest.json",
    )
    for script in bundle.script_paths:
        copy_into_container(container, script, f"{_CHECK_ROOT}/checks/{script.name}")


def _exec_checker(
    container: RunningContainer,
    argv: list[str],
    timeout_seconds: int,
) -> ExecResult:
    command = [
        "docker",
        "exec",
        "--user",
        f"{_CHECKER_UID}:{_CHECKER_UID}",
        "--workdir",
        _CHECK_ROOT,
        "--env",
        f"TMPDIR={_SCRATCH_ROOT}",
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
        stdout = _bounded_stream(completed.stdout)
        stderr = _bounded_stream(completed.stderr)
        timed_out = exit_code in {124, 137}
    except subprocess.TimeoutExpired as error:
        exit_code = None
        stdout = _bounded_stream(error.stdout)
        stderr = _bounded_stream(error.stderr)
        timed_out = True
    return ExecResult(
        argv=tuple(argv),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=round(time.monotonic() - started, 6),
        timed_out=timed_out,
    )


def _infrastructure_result(
    check: dict[str, Any],
    diagnostic: str,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    return {
        "check_id": check["check_id"],
        "property_id": check["property_id"],
        "status": "inconclusive",
        "exit_code": None,
        "duration_seconds": 0.0,
        "diagnostic": diagnostic,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "evidence_paths": [],
    }


def _execute_checks(
    container: RunningContainer,
    artifact: str | Path,
    bundle: CheckBundle,
    output_dir: str | Path,
) -> CheckResults:
    artifact_path = Path(artifact)
    output = Path(output_dir)
    outputs = output / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    checks = manifest["checks"]
    frozen = freeze_artifact(artifact_path, checker_uid=_CHECKER_UID)
    artifact_hash_before = frozen.tree_hash
    results: list[dict[str, Any]] = []
    setup_error: str | None = None
    if artifact_hash_before != bundle.artifact_hash:
        setup_error = "checker bundle artifact hash does not match mounted artifact"
    else:
        try:
            _docker_setup(container, bundle)
        except (OSError, subprocess.SubprocessError) as error:
            setup_error = f"checker infrastructure setup failed: {error}"
    for check in checks:
        check_id = check["check_id"]
        stdout_relative = Path("outputs") / f"{check_id}.stdout"
        stderr_relative = Path("outputs") / f"{check_id}.stderr"
        stdout_path = output / stdout_relative
        stderr_path = output / stderr_relative
        if setup_error is not None:
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(setup_error + "\n", encoding="utf-8")
            results.append(
                _infrastructure_result(
                    check, setup_error, stdout_relative, stderr_relative
                )
            )
            continue
        try:
            execution = _exec_checker(
                container,
                list(check["argv"]),
                check["timeout_seconds"],
            )
        except (OSError, subprocess.SubprocessError) as error:
            diagnostic = f"checker infrastructure execution failed: {error}"
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(diagnostic + "\n", encoding="utf-8")
            results.append(
                _infrastructure_result(
                    check, diagnostic, stdout_relative, stderr_relative
                )
            )
            continue
        stdout_path.write_text(execution.stdout, encoding="utf-8")
        stderr_path.write_text(execution.stderr, encoding="utf-8")
        results.append(
            interpret_checker_result(
                check,
                execution,
                stdout_relative,
                stderr_relative,
            )
        )
    artifact_hash_after = tree_hash(artifact_path)
    artifact_unchanged = artifact_hash_after == artifact_hash_before
    if not artifact_unchanged:
        results = [
            {
                **result,
                "status": "inconclusive",
                "diagnostic": "artifact changed during checker execution; outcome invalidated",
            }
            for result in results
        ]
    manifest_hash = canonical_json_hash(manifest)
    result_value = {
        "run_id": manifest["run_id"],
        "check_bundle_hash": manifest_hash,
        "artifact_hash_before": artifact_hash_before,
        "artifact_hash_after": artifact_hash_after,
        "artifact_unchanged": artifact_unchanged,
        "results": results,
    }
    results_path = output / "check_results.json"
    record = CheckResults(
        results_id="results-" + canonical_json_hash(result_value),
        results_path=results_path,
        **result_value,
    )
    atomic_write_json(results_path, record.to_dict())
    return record


def execute_checks(
    container: RunningContainer,
    artifact: str | Path,
    bundle: CheckBundle,
    output_dir: str | Path,
) -> CheckResults:
    output = Path(output_dir)
    failed = True
    try:
        result = _execute_checks(container, artifact, bundle, output)
        failed = False
        return result
    finally:
        cleanup = remove_container(container)
        atomic_write_json(
            output / "cleanup.json",
            {
                "success": cleanup.success,
                "removed": cleanup.removed,
                "stderr": cleanup.stderr,
            },
        )
        if not cleanup.success and not failed:
            raise RuntimeError("checker container cleanup failed")
