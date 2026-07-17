from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Callable
import uuid

from ..records import (
    CheckBundle,
    CheckResults,
    ExperimentConfig,
    PatchAttempt,
    RunRecord,
    SkillVersion,
    TestCase,
)
from ..runtime.artifacts import freeze_artifact
from ..runtime.docker import RunningContainer, ContainerSpec, exec_task, start_task_container
from ..runtime.pi import PiRequest, PiResult, _load_usage, run_pi
from ..runtime.providers import (
    estimate_cost,
    qualified_model,
    resolve_model,
    write_pi_models,
)
from ..storage import atomic_write_json, canonical_json_hash, file_hash, tree_hash


_PROPERTY_ID = re.compile(r"P[1-9][0-9]*")
SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]
PiRunner = Callable[[PiRequest], PiResult]


def accept_patch(
    before: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    replay: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    regressions: list[list[dict[str, Any]] | tuple[dict[str, Any], ...]],
) -> str:
    all_after = [*replay, *(item for group in regressions for item in group)]
    if any(
        item.get("status") == "inconclusive"
        and "infrastructure" in str(item.get("diagnostic", "")).lower()
        for item in all_after
    ):
        return "unresolved"
    replay_by_id = {item.get("check_id"): item for item in replay}
    if len(replay_by_id) != len(replay) or set(replay_by_id) != {
        item.get("check_id") for item in before
    }:
        return "unresolved"
    repaired = False
    for prior in before:
        after_status = replay_by_id[prior.get("check_id")].get("status")
        if prior.get("status") == "fail":
            if after_status != "pass":
                return "rejected"
            repaired = True
        elif prior.get("status") == "pass" and after_status != "pass":
            return "rejected"
    if any(item.get("status") != "pass" for item in all_after[len(replay) :]):
        return "rejected"
    return "accepted" if repaired else "rejected"


def _copy_file(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"evidence source is not a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(path.stat().st_mode & ~0o222)
    root.chmod(root.stat().st_mode & ~0o222)


def build_patch_evidence(
    method: str,
    state: dict[str, Any],
    skill: SkillVersion,
    test: TestCase,
    run: RunRecord,
    check_bundle: CheckBundle,
    results: CheckResults,
    output_dir: str | Path,
) -> tuple[Path, str]:
    if method not in {"random", "verigrey", "skillrace"}:
        raise ValueError("unknown patch evidence method")
    if run.skill_id != skill.skill_id or run.skill_version_id != skill.version_id:
        raise ValueError("run and skill identity do not match")
    if run.test_id != test.test_id:
        raise ValueError("run and test identity do not match")
    if check_bundle.run_id != run.run_id or results.run_id != run.run_id:
        raise ValueError("run and checker identities do not match")
    if (
        run.artifact_hash != tree_hash(run.artifact_path)
        or check_bundle.artifact_hash != run.artifact_hash
        or results.artifact_hash_before != run.artifact_hash
        or results.artifact_hash_after != run.artifact_hash
        or not results.artifact_unchanged
    ):
        raise ValueError("artifact provenance does not match")
    manifest_value = json.loads(check_bundle.manifest_path.read_text(encoding="utf-8"))
    if canonical_json_hash(manifest_value) != results.check_bundle_hash:
        raise ValueError("check bundle does not match authoritative results")
    if skill.tree_hash != tree_hash(skill.directory_path):
        raise ValueError("skill hash does not match")
    if (
        test.prompt_hash != file_hash(test.prompt_path)
        or test.environment_hash != tree_hash(test.environment_directory)
        or test.nl_check_hash != file_hash(test.nl_check_path)
    ):
        raise ValueError("test hashes do not match")
    output = Path(output_dir)
    if output.exists():
        raise ValueError("patch evidence output already exists")
    common = output / "common"
    shutil.copytree(skill.directory_path, common / "skill")
    atomic_write_json(common / "skill" / "skill-version.json", skill.to_dict())
    _copy_file(test.prompt_path, common / "test" / "prompt.txt")
    shutil.copytree(test.environment_directory, common / "test" / "environment")
    _copy_file(test.nl_check_path, common / "test" / "nl_checks.json")
    _copy_file(test.proposal_receipt, common / "test" / "proposal-receipt.json")
    atomic_write_json(common / "test" / "test-case.json", test.to_dict())
    shutil.copytree(run.artifact_path, common / "artifact")
    atomic_write_json(common / "run" / "run.json", run.to_dict())
    for source, name in (
        (run.trace_path, "trace.jsonl"),
        (run.tool_log_path, "tool_outputs.jsonl"),
        (run.stdout_path, "stdout.txt"),
        (run.stderr_path, "stderr.txt"),
    ):
        _copy_file(source, common / "run" / name)
    _copy_file(check_bundle.manifest_path, common / "checks" / "check_manifest.json")
    for script in check_bundle.script_paths:
        _copy_file(script, common / "checks" / "scripts" / script.name)
    _copy_file(
        check_bundle.codex_receipt_path,
        common / "checks" / "codex-receipt.jsonl",
    )
    _copy_file(results.results_path, common / "results" / "check_results.json")
    result_root = results.results_path.parent.resolve()
    copied_result_streams: list[str] = []
    for item in results.results:
        for field in ("stdout_path", "stderr_path"):
            source = (results.results_path.parent / item[field]).resolve()
            try:
                relative = source.relative_to(result_root)
            except ValueError:
                raise ValueError("authoritative result stream escapes results directory") from None
            _copy_file(source, common / "results" / relative)
            copied_result_streams.append(
                (Path("common") / "results" / relative).as_posix()
            )
    if method == "verigrey":
        observation = state.get("last_observation")
        if not isinstance(observation, dict):
            raise ValueError("VeriGrey patch evidence lacks last observation")
        atomic_write_json(output / "method" / "verigrey.json", observation)
    elif method == "skillrace":
        if set(state) != {"episodes", "tree", "branch"}:
            raise ValueError("SkillRACE patch evidence fields are invalid")
        atomic_write_json(output / "method" / "skillrace.json", state)
    elif state:
        raise ValueError("Random patch evidence must not include method state")
    atomic_write_json(
        output / "evidence.json",
        {
            "schema": "skillrace-patch-evidence/1",
            "method": method,
            "run_id": run.run_id,
            "common_hash": tree_hash(common),
            "task_prompt": test.prompt_path.read_text(encoding="utf-8"),
            "authoritative_results": [dict(item) for item in results.results],
            "method_evidence": (
                None
                if method == "random"
                else f"method/{method}.json"
            ),
            "files": {
                "skill": "common/skill/SKILL.md",
                "test_prompt": "common/test/prompt.txt",
                "environment": "common/test/environment",
                "artifact": "common/artifact",
                "trace": "common/run/trace.jsonl",
                "tool_outputs": "common/run/tool_outputs.jsonl",
                "nl_checks": "common/test/nl_checks.json",
                "check_manifest": "common/checks/check_manifest.json",
                "check_scripts": [
                    f"common/checks/scripts/{script.name}"
                    for script in check_bundle.script_paths
                ],
                "check_results": "common/results/check_results.json",
                "result_streams": sorted(set(copied_result_streams)),
                "method": (
                    None if method == "random" else f"method/{method}.json"
                ),
            },
        },
    )
    _make_tree_read_only(output)
    return output, tree_hash(output)


def _patch_trace_is_ordered(trace_path: Path) -> bool:
    read_skill = False
    read_evidence = False
    explained = False
    edited = False
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message", {})
        if message.get("role") != "assistant":
            continue
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"thinking", "text"} and any(
                isinstance(item.get(field), str) and item[field].strip()
                for field in ("thinking", "text")
            ):
                explained = True
            if item.get("type") != "toolCall":
                continue
            name = item.get("name")
            arguments = item.get("arguments", {})
            path = arguments.get("path", "") if isinstance(arguments, dict) else ""
            if name == "read" and path == "/skill/SKILL.md":
                read_skill = True
            elif name == "read" and isinstance(path, str) and path.startswith("/evidence/"):
                read_evidence = True
            elif name == "edit":
                if path != "/skill/SKILL.md" or not (
                    read_skill and read_evidence and explained
                ):
                    return False
                edited = True
    return edited


def _relative_file_hashes(root: Path, *, omit_skill: bool = False) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): file_hash(path)
        for path in root.rglob("*")
        if path.is_file()
        and not (omit_skill and path.relative_to(root).as_posix() == "SKILL.md")
    }


def patch_skill(
    skill: SkillVersion,
    evidence: str | Path,
    method: str,
    config: ExperimentConfig,
    output_dir: str | Path,
    *,
    pi_runner: PiRunner = run_pi,
) -> PatchAttempt:
    if method not in {"random", "verigrey", "skillrace"}:
        raise ValueError("unknown patch method")
    if skill.tree_hash != tree_hash(skill.directory_path):
        raise ValueError("patch input skill hash does not match")
    evidence_path = Path(evidence)
    evidence_record = json.loads(
        (evidence_path / "evidence.json").read_text(encoding="utf-8")
    )
    if evidence_record.get("method") != method:
        raise ValueError("patch method does not match evidence")
    evidence_hash = tree_hash(evidence_path)
    output = Path(output_dir)
    if output.exists():
        raise ValueError("patch output already exists")
    candidate = output / "candidate"
    candidate.parent.mkdir(parents=True)
    shutil.copytree(skill.directory_path, candidate)
    original_other_files = _relative_file_hashes(skill.directory_path, omit_skill=True)
    original_skill_hash = file_hash(skill.directory_path / "SKILL.md")
    prompt_path = output / "prompt.txt"
    prompt_path.write_text(
        "Patch the mounted coding-agent skill using only the mounted failure evidence. "
        "First read /skill/SKILL.md and /evidence/evidence.json. evidence.json includes the "
        "exact task and authoritative results; their audit copies are at "
        "/evidence/common/test/prompt.txt and /evidence/common/results/check_results.json. "
        "The authoritative executable check defines the required behavior even when the "
        "current skill contradicts it. Do not reread evidence already included in evidence.json. "
        "After those two reads, edit immediately when the diagnostic identifies the failure; "
        "otherwise read at most one essential NL-check or executable-check file, then edit. "
        "Briefly explain the failure in your saved reasoning before editing. "
        "Edit only /skill/SKILL.md, make a small general correction, do not copy or memorize "
        "test-specific values, do not execute the benchmark, and stop after the edit.\n",
        encoding="utf-8",
    )
    pi_output = output / "pi"
    result = pi_runner(
        PiRequest(
            operation_id=f"patch.{uuid.uuid4().hex}",
            provider=config.provider,
            model=config.model_id,
            prompt_path=prompt_path,
            output_dir=pi_output,
            image=config.docker_image,
            allowed_tools=("read", "edit"),
            max_turns=config.role_budgets["patcher"],
            timeout_seconds=config.timeouts["patch"],
            mounts=((candidate, "/skill", "rw"), (evidence_path, "/evidence", "ro")),
        )
    )
    candidate_hash = tree_hash(candidate)
    valid = result.status == "completed"
    valid = valid and tree_hash(evidence_path) == evidence_hash
    valid = valid and _relative_file_hashes(candidate, omit_skill=True) == original_other_files
    candidate_skill = candidate / "SKILL.md"
    valid = valid and candidate_skill.is_file()
    if candidate_skill.is_file():
        value = candidate_skill.read_text(encoding="utf-8")
        valid = valid and bool(value.strip()) and "\x00" not in value
        valid = valid and file_hash(candidate_skill) != original_skill_hash
    valid = valid and result.trace_path.is_file()
    if result.trace_path.is_file():
        valid = valid and _patch_trace_is_ordered(result.trace_path)
    if result.status == "timeout":
        patch_status = "patch_timeout"
    elif valid:
        patch_status = "patched"
    else:
        patch_status = "patch_invalid"
    attempt = PatchAttempt(
        patch_attempt_id="patch-" + uuid.uuid4().hex,
        input_skill_hash=skill.tree_hash,
        evidence_bundle_hash=evidence_hash,
        method=method,
        model_id=config.model_id,
        pi_trace_path=result.trace_path,
        cost_receipt_path=result.receipt_path,
        candidate_skill_hash=candidate_hash,
        patch_status=patch_status,
        replay_path=None,
        acceptance_status="pending",
    )
    atomic_write_json(output / "patch-attempt.json", attempt.to_dict())
    return attempt


def _assistant_skill(trace_path: Path) -> str:
    responses: list[str] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message", {})
        if message.get("role") != "assistant":
            continue
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        text = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
        if text:
            responses.append(text)
    if not responses:
        raise ValueError("generated SKILL.md is empty")
    skill = responses[-1].strip()
    if not skill or "\x00" in skill or skill.startswith("```"):
        raise ValueError("generated SKILL.md is empty, fenced, or contains NUL")
    lines = skill.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("generated SKILL.md must start with YAML front matter")
    try:
        closing = next(
            index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"
        )
    except StopIteration as error:
        raise ValueError("generated SKILL.md front matter is not closed") from error
    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
    if not metadata.get("name") or not metadata.get("description"):
        raise ValueError("generated SKILL.md needs nonempty name and description")
    if not "\n".join(lines[closing + 1 :]).strip():
        raise ValueError("generated SKILL.md body is empty")
    return skill + "\n"


def generate_base_skill(
    scenario: str | Path,
    config: ExperimentConfig,
    output_dir: str | Path,
    *,
    pi_runner: PiRunner = run_pi,
) -> SkillVersion:
    scenario_path = Path(scenario)
    if scenario_path.is_symlink() or not scenario_path.is_file():
        raise ValueError("scenario must be a regular file")
    scenario_text = scenario_path.read_text(encoding="utf-8")
    if not scenario_text.strip():
        raise ValueError("scenario must be nonempty")
    output = Path(output_dir)
    if output.exists():
        raise ValueError("base-skill output directory already exists")
    generation = output / "generation"
    pi_output = generation / "pi"
    generation.mkdir(parents=True)
    prompt_path = generation / "prompt.txt"
    prompt_path.write_text(
        "Create one concise, general coding-agent skill for the public scenario below. "
        "Include practical steps, validation, and guardrails without inventing evaluation "
        "cases. Return only the complete SKILL.md. Do not begin with prose or a Markdown fence. "
        "The response must begin with exactly this shape:\n"
        "---\nname: concise-name\ndescription: concise description\n---\n"
        "Then write a nonempty Markdown body. Replace the example metadata values with "
        "scenario-specific values. Do not use tools.\n\n"
        f"PUBLIC SCENARIO:\n---\n{scenario_text.rstrip()}\n---\n",
        encoding="utf-8",
    )
    result = pi_runner(
        PiRequest(
            operation_id=f"base-skill.{config.experiment_id}.{uuid.uuid4().hex}",
            provider=config.provider,
            model=config.model_id,
            prompt_path=prompt_path,
            output_dir=pi_output,
            image=config.docker_image,
            allowed_tools=("read",),
            max_turns=config.role_budgets["skill_generator"],
            timeout_seconds=config.timeouts["pi"],
        )
    )
    if result.status != "completed":
        raise RuntimeError(f"Pi base-skill generation failed: {result.status}")
    skill_text = _assistant_skill(result.trace_path)
    base = output / "base"
    base.mkdir()
    skill_path = base / "SKILL.md"
    skill_path.write_text(skill_text, encoding="utf-8")
    copies: dict[str, str] = {}
    for method in config.methods:
        method_dir = output / "methods" / method
        method_dir.mkdir(parents=True)
        shutil.copyfile(skill_path, method_dir / "SKILL.md")
        copies[method] = str(method_dir)
    version = SkillVersion(
        skill_id=f"{config.experiment_id}-base",
        version_id="S0",
        parent_version_id=None,
        directory_path=base,
        tree_hash=tree_hash(base),
        creation_role="skill_generator",
        model_id=config.model_id,
        receipt_path=result.receipt_path,
    )
    atomic_write_json(output / "skill-version.json", version.to_dict())
    atomic_write_json(
        output / "generation.json",
        {
            "schema": "skillrace-base-skill-generation/1",
            "scenario_path": str(scenario_path),
            "model": config.model_id,
            "trace_path": str(result.trace_path),
            "pi_receipt_path": str(result.receipt_path),
            "usage": result.usage,
            "method_copy_paths": copies,
        },
    )
    return version


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
    selected_model = resolve_model(config.provider, config.model_id)
    models_path = write_pi_models(runtime_evidence / "models.json", selected_model)
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
                (models_path, "/home/node/.pi/agent/models.json", "ro"),
            ),
            network=config.network_policy,
            cpus=str(cpus),
            memory=f"{memory_mb}m",
            working_directory="/workspace",
            user=f"{os.getuid()}:{os.getgid()}",
            environment=(selected_model.key_environment,),
        )
    )
    prompt = test.prompt_path.read_text(encoding="utf-8")
    started_at = datetime.now(UTC).isoformat()
    result = exec_task(
        running,
        [
            "pi",
            "--provider",
            selected_model.provider,
            "--model",
            selected_model.upstream_model,
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
            "/skill/SKILL.md",
            prompt,
        ],
        timeout_seconds=config.timeouts["pi"],
    )
    ended_at = datetime.now(UTC).isoformat()
    secret = os.environ.get(selected_model.key_environment, "")
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
    estimated_cost = estimate_cost(selected_model, usage)
    provider_receipt = runtime_evidence / "provider.json"
    atomic_write_json(
        provider_receipt,
        {
            "schema": "skillrace-provider-usage/1",
            "provider": selected_model.provider,
            "model": selected_model.friendly_model,
            "qualified_model": qualified_model(selected_model),
            "upstream_model": selected_model.upstream_model,
            "usage": usage,
            "estimated_cost_usd": (
                str(estimated_cost) if estimated_cost is not None else "unpriced"
            ),
        },
    )
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
        provider_receipt_paths=(provider_receipt,),
        cost_totals=usage,
    )
    atomic_write_json(output / "run.json", run.to_dict())
    return run


def replay(
    skill: SkillVersion,
    test: TestCase,
    bundle: CheckBundle,
    config: ExperimentConfig,
    output_dir: str | Path,
    *,
    agent_runner: Callable[..., RunRecord] = run_agent,
    check_runner: Callable[..., CheckResults] | None = None,
) -> CheckResults:
    if check_runner is None:
        from ..verification.executor import execute_checks

        check_runner = execute_checks
    output = Path(output_dir)
    if output.exists():
        raise ValueError("replay output already exists")
    output.mkdir(parents=True)
    fresh_run = agent_runner(skill, test, config, output / "run")
    atomic_write_json(output / "run" / "run.json", fresh_run.to_dict())
    if fresh_run.termination_status != "completed":
        raise RuntimeError(
            f"replay agent did not complete: {fresh_run.termination_status}"
        )
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    manifest["run_id"] = fresh_run.run_id
    manifest["artifact_hash"] = fresh_run.artifact_hash
    rebound_root = output / "check-bundle"
    rebound_scripts = rebound_root / "checks"
    rebound_scripts.mkdir(parents=True)
    copied_scripts: list[Path] = []
    for script in bundle.script_paths:
        copied = rebound_scripts / script.name
        shutil.copyfile(script, copied)
        copied_scripts.append(copied)
    rebound_manifest = rebound_root / "check_manifest.json"
    atomic_write_json(rebound_manifest, manifest)
    rebound_receipt = rebound_root / "codex-receipt.jsonl"
    shutil.copyfile(bundle.codex_receipt_path, rebound_receipt)
    rebound = CheckBundle(
        bundle_id="bundle-" + canonical_json_hash(manifest),
        run_id=fresh_run.run_id,
        artifact_hash=fresh_run.artifact_hash,
        input_hashes={**bundle.input_hashes, "artifact": fresh_run.artifact_hash},
        manifest_path=rebound_manifest,
        script_paths=tuple(copied_scripts),
        codex_receipt_path=rebound_receipt,
    )
    atomic_write_json(output / "check-bundle.json", rebound.to_dict())
    running = RunningContainer(
        fresh_run.container_id,
        f"skillrace-replay-{fresh_run.run_id}",
        fresh_run.image_id,
    )
    results = check_runner(
        running,
        fresh_run.artifact_path,
        rebound,
        output / "results",
    )
    atomic_write_json(
        output / "replay.json",
        {
            "schema": "skillrace-exact-replay/1",
            "run_id": fresh_run.run_id,
            "skill_version_id": skill.version_id,
            "test_id": test.test_id,
            "source_bundle_id": bundle.bundle_id,
            "rebound_bundle_id": rebound.bundle_id,
            "results_id": results.results_id,
        },
    )
    return results
