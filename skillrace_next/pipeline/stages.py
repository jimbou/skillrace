from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable
import uuid

from ..records import ExperimentConfig, RunRecord, SkillVersion, TestCase
from ..runtime.artifacts import freeze_artifact
from ..runtime.docker import ContainerSpec, exec_task, start_task_container
from ..runtime.pi import _load_usage
from ..storage import atomic_write_json, file_hash, tree_hash


_PROPERTY_ID = re.compile(r"P[1-9][0-9]*")
SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]


def validate_nl_checks(path: str | Path) -> list[dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list) or not value:
        raise ValueError("NL checks must be a nonempty list")
    checks: list[dict[str, Any]] = []
    property_ids: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each NL check must be an object")
        property_id = item.get("property_id")
        description = item.get("description")
        if not isinstance(property_id, str) or not _PROPERTY_ID.fullmatch(property_id):
            raise ValueError("NL check property_id is malformed")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("NL check description must be nonempty")
        property_ids.append(property_id)
        checks.append(dict(item))
    if len(set(property_ids)) != len(property_ids):
        raise ValueError("NL check property IDs must be unique")
    return checks


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def validate_test(
    test: TestCase,
    config: ExperimentConfig,
    docker_runner: SubprocessRunner = subprocess.run,
) -> TestCase:
    try:
        root = config.suite_path
        paths = (
            test.prompt_path,
            test.environment_directory,
            test.nl_check_path,
            test.proposal_receipt,
        )
        if any(not _inside(path, root) for path in paths):
            raise ValueError("test path is outside the configured suite root")
        if not test.prompt_path.is_file():
            raise ValueError("prompt file is missing")
        if not test.prompt_path.read_text(encoding="utf-8").strip():
            raise ValueError("prompt is empty")
        if not test.environment_directory.is_dir():
            raise ValueError("environment directory is missing")
        if not test.nl_check_path.is_file():
            raise ValueError("NL-check file is missing")
        if not test.proposal_receipt.is_file():
            raise ValueError("proposal receipt is missing")
        if file_hash(test.prompt_path) != test.prompt_hash:
            raise ValueError("prompt hash mismatch")
        if tree_hash(test.environment_directory) != test.environment_hash:
            raise ValueError("environment hash mismatch")
        if file_hash(test.nl_check_path) != test.nl_check_hash:
            raise ValueError("NL-check hash mismatch")
        validate_nl_checks(test.nl_check_path)
        dockerfile = test.environment_directory / "Dockerfile"
        if not dockerfile.is_file():
            raise ValueError("environment Dockerfile is missing")
        sanity_path = test.environment_directory / "sanity.json"
        sanity = json.loads(sanity_path.read_text(encoding="utf-8"))
        if not isinstance(sanity, dict) or sanity.get("status") != "pass":
            raise ValueError("environment sanity receipt is invalid")
        completed = docker_runner(
            ["docker", "build", "-q", str(test.environment_directory.resolve())],
            check=False,
            capture_output=True,
            text=True,
            timeout=config.timeouts["docker"],
        )
        if completed.returncode != 0:
            diagnostic = str(completed.stderr or completed.stdout or "")[-500:]
            raise ValueError(f"Docker build failed: {diagnostic}")
        output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if not output_lines:
            raise ValueError("Docker build did not return an image ID")
        return replace(
            test,
            validation_status="valid",
            validation_diagnostic="validated",
            container_image_id=output_lines[-1],
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return replace(
            test,
            validation_status="invalid_test",
            validation_diagnostic=str(error),
            container_image_id="",
        )


def run_agent(
    skill: SkillVersion,
    test: TestCase,
    config: ExperimentConfig,
    output_dir: str | Path,
) -> RunRecord:
    if test.validation_status != "valid" or not test.container_image_id:
        raise ValueError("run_agent requires a validated test image")
    output = Path(output_dir)
    artifact = output / "artifact"
    runtime_evidence = output / "runtime"
    artifact.mkdir(parents=True)
    runtime_evidence.mkdir()
    memory_mb = config.resource_limits.get("memory_mb", 512)
    cpus = config.resource_limits.get("cpus", "1")
    running = start_task_container(
        ContainerSpec(
            name="skillrace-run-" + uuid.uuid4().hex[:16],
            image=test.container_image_id,
            image_id=test.container_image_id,
            mounts=(
                (artifact, "/workspace", "rw"),
                (runtime_evidence, "/evidence", "rw"),
                (skill.directory_path, "/skill", "ro"),
            ),
            network=config.network_policy,
            cpus=str(cpus),
            memory=f"{memory_mb}m",
            working_directory="/workspace",
            user=f"{os.getuid()}:{os.getgid()}",
            environment=("yunwu_key",),
        )
    )
    prompt = test.prompt_path.read_text(encoding="utf-8")
    started_at = datetime.now(UTC).isoformat()
    result = exec_task(
        running,
        [
            "pi",
            "--provider",
            "yunwu",
            "--model",
            config.model_id,
            "--thinking",
            "medium",
            "--print",
            "--tools",
            "read,bash,edit,write",
            "--no-extensions",
            "--no-prompt-templates",
            "--no-themes",
            "--session",
            "/evidence/trace.jsonl",
            "--skill",
            "/skill",
            prompt,
        ],
        timeout_seconds=config.timeouts["pi"],
    )
    ended_at = datetime.now(UTC).isoformat()
    secret = os.environ.get("yunwu_key", "")
    stdout = result.stdout.replace(secret, "[REDACTED]") if secret else result.stdout
    stderr = result.stderr.replace(secret, "[REDACTED]") if secret else result.stderr
    stdout_path = runtime_evidence / "stdout.txt"
    stderr_path = runtime_evidence / "stderr.txt"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    trace_path = runtime_evidence / "trace.jsonl"
    tool_log_path = runtime_evidence / "tool_outputs.jsonl"
    tool_records: list[dict[str, Any]] = []
    if trace_path.is_file():
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                record.get("type") == "message"
                and record.get("message", {}).get("role") == "toolResult"
            ):
                tool_records.append(record)
    tool_log_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in tool_records),
        encoding="utf-8",
    )
    usage = _load_usage(runtime_evidence, trace_path)
    frozen = freeze_artifact(artifact, checker_uid=65534)
    if result.timed_out:
        termination_status = "agent_timeout"
    elif result.exit_code == 0:
        termination_status = "completed"
    elif "provider" in stderr.lower():
        termination_status = "provider_error"
    else:
        termination_status = "container_error"
    run = RunRecord(
        run_id="run-" + uuid.uuid4().hex,
        test_id=test.test_id,
        skill_id=skill.skill_id,
        skill_version_id=skill.version_id,
        method=test.origin_method,
        model_id=config.model_id,
        budget=config.role_budgets["weak_agent"],
        container_id=running.container_id,
        image_id=running.image_id,
        started_at=started_at,
        ended_at=ended_at,
        termination_status=termination_status,
        artifact_path=artifact,
        artifact_hash=frozen.tree_hash,
        trace_path=trace_path,
        tool_log_path=tool_log_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        provider_receipt_paths=(),
        cost_totals=usage,
    )
    atomic_write_json(output / "run.json", run.to_dict())
    return run
