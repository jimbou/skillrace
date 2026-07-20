import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
from typing import Any, Callable

from ..records import CheckBundle
from ..storage import atomic_write_json, canonical_json_hash, file_hash, tree_hash


_ROOT_CAUSE_CATEGORIES = {
    "instruction_missing",
    "instruction_ambiguous",
    "wrong_workflow",
    "tool_misuse",
    "validation_missing",
    "format_contract",
    "environment_assumption",
    "other",
}
CodexRunner = Callable[..., subprocess.CompletedProcess[str]]
_DOCKER_COMMAND = re.compile(
    r"(?:^|(?:&&|\|\||[;|(\n])\s*|(?:\s-c|\s-lc)\s+[\"']?)"
    r"(?:sudo\s+)?(?:/[A-Za-z0-9_./-]+/)?docker(?=$|[\s;&|)\"'])",
    re.IGNORECASE,
)


def command_invokes_docker(command: str) -> bool:
    return _DOCKER_COMMAND.search(command) is not None


def validate_check_manifest(
    path: str | Path,
    nl_checks: list[dict[str, Any]],
    artifact_hash: str,
) -> CheckBundle:
    manifest_path = Path(path)
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("check manifest must be an object")
    if set(value) != {"schema", "run_id", "artifact_hash", "checks", "uncovered"}:
        raise ValueError("check manifest fields are invalid")
    if value["schema"] != "skillrace-check-bundle/1":
        raise ValueError("check manifest schema is invalid")
    if not isinstance(value["run_id"], str) or not value["run_id"]:
        raise ValueError("check manifest run_id is invalid")
    if value["artifact_hash"] != artifact_hash:
        raise ValueError("check manifest artifact hash does not match")
    checks = value["checks"]
    uncovered = value["uncovered"]
    if not isinstance(checks, list) or not isinstance(uncovered, list):
        raise ValueError("checks and uncovered must be lists")
    known_properties = {
        check.get("property_id")
        for check in nl_checks
        if isinstance(check, dict) and isinstance(check.get("property_id"), str)
    }
    if len(known_properties) != len(nl_checks):
        raise ValueError("supplied NL checks have invalid or duplicate property IDs")
    declared_paths: list[Path] = []
    covered_properties: set[str] = set()
    check_ids: set[str] = set()
    expected_check_fields = {
        "check_id",
        "property_id",
        "script",
        "argv",
        "timeout_seconds",
        "purpose",
        "pass_condition",
        "failure_condition",
        "root_cause_category",
    }
    output = manifest_path.parent
    for check in checks:
        if not isinstance(check, dict) or set(check) != expected_check_fields:
            raise ValueError("declared check fields are invalid")
        check_id = check["check_id"]
        property_id = check["property_id"]
        if not isinstance(check_id, str) or not check_id or check_id in check_ids:
            raise ValueError("check IDs must be nonempty and unique")
        check_ids.add(check_id)
        if property_id not in known_properties:
            raise ValueError("declared check references an unknown property")
        covered_properties.add(property_id)
        raw_script = check["script"]
        if not isinstance(raw_script, str):
            raise ValueError("check script must be a relative path")
        relative = PurePosixPath(raw_script)
        if relative.is_absolute() or ".." in relative.parts or relative.parts[:1] != ("checks",):
            raise ValueError("check script escapes the checks directory")
        script = output.joinpath(*relative.parts)
        try:
            script.resolve().relative_to(output.resolve())
        except ValueError:
            raise ValueError("check script escapes verifier output") from None
        if not script.is_file():
            raise ValueError("declared check script is missing")
        declared_paths.append(script)
        argv = check["argv"]
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(argument, str) and argument for argument in argv)
        ):
            raise ValueError("check argv must be a nonempty string list")
        declared_script_arguments = {
            raw_script,
            f"/tmp/skillrace-checks/{raw_script}",
        }
        if not any(argument in declared_script_arguments for argument in argv):
            raise ValueError("check argv must invoke its declared script")
        timeout = check["timeout_seconds"]
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 60:
            raise ValueError("check timeout must be in 1..60")
        for name in ("purpose", "pass_condition", "failure_condition"):
            if not isinstance(check[name], str) or not check[name].strip():
                raise ValueError(f"check {name} must be nonempty")
        if check["root_cause_category"] not in _ROOT_CAUSE_CATEGORIES:
            raise ValueError("check root-cause category is invalid")
    uncovered_properties: set[str] = set()
    for item in uncovered:
        if not isinstance(item, dict) or set(item) != {"property_id", "reason"}:
            raise ValueError("uncovered entry fields are invalid")
        property_id = item["property_id"]
        reason = item["reason"]
        if property_id not in known_properties or property_id in uncovered_properties:
            raise ValueError("uncovered property is unknown or duplicated")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("uncovered reason must be nonempty")
        uncovered_properties.add(property_id)
    if covered_properties & uncovered_properties:
        raise ValueError("a property cannot be both checked and uncovered")
    if covered_properties | uncovered_properties != known_properties:
        raise ValueError("every NL property must be checked or uncovered")
    actual_scripts = {
        candidate.resolve()
        for candidate in (output / "checks").rglob("*")
        if candidate.is_file()
    } if (output / "checks").is_dir() else set()
    if actual_scripts != {script.resolve() for script in declared_paths}:
        raise ValueError("checks directory contains undeclared scripts")
    return CheckBundle(
        bundle_id="bundle-" + canonical_json_hash(value),
        run_id=value["run_id"],
        artifact_hash=artifact_hash,
        input_hashes={
            "artifact": artifact_hash,
            "nl_checks": canonical_json_hash(nl_checks),
        },
        manifest_path=manifest_path,
        script_paths=tuple(declared_paths),
        codex_receipt_path=output / "codex-events.jsonl",
    )


def _make_read_only(path: Path) -> None:
    children = sorted(
        path.rglob("*"),
        key=lambda child: len(child.relative_to(path).parts),
        reverse=True,
    )
    for child in children:
        child.chmod(child.stat().st_mode & ~0o222)
    path.chmod(path.stat().st_mode & ~0o222)


def _invoke_codex(
    workspace: Path,
    config: Any,
    prompt: str,
    runner: CodexRunner,
) -> subprocess.CompletedProcess[str]:
    output = workspace / "output"
    command = [
        *config.verifier_command,
        "--model",
        config.verifier_model,
        "--sandbox",
        "workspace-write",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "-c",
        f'model_reasoning_effort="{config.verifier_reasoning}"',
        prompt,
    ]
    environment = os.environ.copy()
    environment.pop("yunwu_key", None)
    environment.pop("LAB_KEY_UNLIMITED", None)
    environment.pop("DOCKER_CONTEXT", None)
    environment["DOCKER_HOST"] = "unix:///nonexistent.sock"
    return runner(
        command,
        cwd=output,
        env=environment,
        capture_output=True,
        text=True,
        timeout=config.timeouts["codex"],
        check=False,
    )


def author_checks(
    workspace: str | Path,
    config: Any,
    injected_subprocess_runner: CodexRunner = subprocess.run,
) -> CheckBundle:
    root = Path(workspace)
    guide = root / "GUIDE.md"
    input_dir = root / "input"
    output = root / "output"
    if not guide.is_file() or not input_dir.is_dir() or not output.is_dir():
        raise ValueError("verifier workspace is incomplete")
    nl_checks_value = json.loads(
        (input_dir / "nl_checks.json").read_text(encoding="utf-8")
    )
    if not isinstance(nl_checks_value, list):
        raise ValueError("verifier NL checks must be a list")
    nl_checks = [dict(item) for item in nl_checks_value if isinstance(item, dict)]
    if len(nl_checks) != len(nl_checks_value):
        raise ValueError("verifier NL checks must contain objects")
    artifact_hash = tree_hash(input_dir / "artifact")
    input_hash_before = tree_hash(input_dir)
    guide_hash_before = file_hash(guide)
    _make_read_only(input_dir)
    guide.chmod(guide.stat().st_mode & ~0o222)
    events_path = output / "codex-events.jsonl"
    stderr_path = output / "codex-stderr.txt"
    prompt = (
        "Read ../GUIDE.md in full, then inspect ../input/. Author executable checks for "
        "every supplied natural-language property. Write only check_manifest.json and "
        "declared scripts under checks/ in the current output directory. Do not modify "
        "inputs, do not repair the artifact, do not use Docker, and do not claim local "
        "exploration is an authoritative verdict."
    )
    last_error: ValueError | None = None
    for attempt in (1, 2):
        completed = _invoke_codex(
            root,
            config,
            prompt,
            injected_subprocess_runner,
        )
        with events_path.open("a", encoding="utf-8") as stream:
            stream.write(completed.stdout or "")
        with stderr_path.open("a", encoding="utf-8") as stream:
            stream.write(completed.stderr or "")
        if tree_hash(input_dir) != input_hash_before or file_hash(guide) != guide_hash_before:
            raise RuntimeError("Codex mutated verifier input")
        if completed.returncode != 0:
            raise RuntimeError(f"Codex verifier exited with {completed.returncode}")
        manifest_path = output / "check_manifest.json"
        try:
            return validate_check_manifest(manifest_path, nl_checks, artifact_hash)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            last_error = ValueError(str(error))
            if manifest_path.is_file():
                shutil.copy2(
                    manifest_path,
                    output / f"invalid-check-manifest-attempt-{attempt}.json",
                )
            if attempt == 1:
                prompt = (
                    "Correct the existing check bundle in the current output directory. "
                    f"The deterministic validator reported: {error}. Read ../GUIDE.md "
                    "again and fix only the bundle structure; do not modify any input or "
                    "use Docker."
                )
    checks_dir = output / "checks"
    if checks_dir.exists():
        shutil.rmtree(checks_dir)
    run_value = json.loads((input_dir / "run.json").read_text(encoding="utf-8"))
    run_id = run_value.get("run_id", "unknown-run") if isinstance(run_value, dict) else "unknown-run"
    atomic_write_json(
        output / "check_manifest.json",
        {
            "schema": "skillrace-check-bundle/1",
            "run_id": run_id,
            "artifact_hash": artifact_hash,
            "checks": [],
            "uncovered": [
                {
                    "property_id": item["property_id"],
                    "reason": f"Codex produced two invalid bundles: {last_error}",
                }
                for item in nl_checks
            ],
        },
    )
    return validate_check_manifest(
        output / "check_manifest.json", nl_checks, artifact_hash
    )
