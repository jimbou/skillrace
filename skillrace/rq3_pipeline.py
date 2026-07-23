"""Runnable, leakage-safe end-to-end orchestrator for the four-condition RQ3 study."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .campaign_protocol import CampaignProtocol
from .closeai import chat, is_nonproduction_chat_fixture, nonproduction_chat_fixture
from .io_utils import atomic_write_json, canonical_json_hash, file_hash
from .model_policy import EXPERIMENT_MODELS
from .revise_skill import package_hash
from .repair_validation import (
    FailureRepairRequest,
    make_model_patcher,
    make_replay_executor,
    repair_campaign_failures,
    repair_failed_execution,
    select_failure_repairs,
    validate_repair_ledger,
)
from .rq3 import (
    FROZEN_FEEDBACK_MAX_BYTES,
    PRODUCERS,
    ManifestMismatchError,
    assert_no_hidden_material,
    evaluate_hidden_scenario,
    feedback_record_from_file,
    load_rq3_manifest,
    project_feedback_set,
    revision_record_from_artifact,
    revise_feedback_set,
    stage_public_scenario,
    verify_rq3_evaluation_artifacts,
)
from .rq3_base import validate_base_generation
from .rq3_campaign import (
    derive_campaign_cost_record,
    prepare_campaign_input_record,
    validate_campaign_artifact,
)
from .rq3_confirmation import (
    ConfirmationRequest,
    confirm_campaign_findings,
    validate_confirmation_ledger,
)
from .rq3_isolation import (
    Phase1Confinement,
    build_phase1_confinement,
    validate_phase1_confinement_record,
)
from .rq3_scenario import load_public_campaign_config
from .scenario_contract import load_scenario
from .skill_eval import HiddenExecutionRequest, execute_hidden_request


@dataclasses.dataclass(frozen=True)
class BaseBuildRequest:
    image: str
    construction_image: str
    model: str
    skill_name: str
    context_dir: pathlib.Path
    containerfile: pathlib.Path
    launch_path: pathlib.Path


@dataclasses.dataclass(frozen=True)
class CampaignLaunchRequest:
    method: str
    skill_name: str
    skill_dir: pathlib.Path
    base_image: str
    properties_path: pathlib.Path
    protocol_path: pathlib.Path
    output_dir: pathlib.Path
    wall_clock: int
    protocol_hash: str
    base_skill_hash: str
    base_package_hash: str
    public_stage_hash: str


def _read(path: pathlib.Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise ManifestMismatchError(f"{label} symlink is forbidden: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestMismatchError(f"cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ManifestMismatchError(f"{label} must be a JSON object: {path}")
    return value


def _clean_env() -> dict[str, str]:
    allowed = (
        "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        "yunwu_key", "SKILLRACE_LEDGER", "DOCKER_HOST", "DOCKER_CONFIG",
        "XDG_RUNTIME_DIR",
    )
    return {name: os.environ[name] for name in allowed if name in os.environ}


def _launch_record(*, role: str, cwd: pathlib.Path, argv: Sequence[str], roots: Sequence[pathlib.Path]) -> dict[str, Any]:
    env = _clean_env()
    return {
        "schema": "skillrace-rq3-public-launch/1",
        "role": role,
        "cwd": str(cwd.resolve()),
        "argv": list(argv),
        "accessible_artifact_roots": [str(path.resolve()) for path in roots],
        "environment": {
            "names": sorted(env),
            "secret_names": sorted(
                name
                for name in env
                if "KEY" in name.upper()
                or "TOKEN" in name.upper()
                or "SECRET" in name.upper()
            ),
            "values_recorded": False,
        },
    }


def _write_or_verify(path: pathlib.Path, value: Mapping[str, Any], label: str) -> None:
    if path.exists():
        if _read(path, label) != value:
            raise ManifestMismatchError(f"{label} identity mismatch: {path}")
    else:
        atomic_write_json(path, dict(value))


def _validate_frozen_protocol(path: pathlib.Path) -> CampaignProtocol:
    protocol = CampaignProtocol.load(path)
    expected = {
        "status": "frozen",
        "budget": 30,
        "bootstrap_count": 10,
        "max_generation_attempts_per_execution": 5,
        "greybox_level": "L1",
        "random_seed": 20260711,
        "seed_generator": {"batch_size": 5, "temperature": 0.9, "build_retries": 4},
    }
    for field, value in expected.items():
        if getattr(protocol, field) != value:
            raise ManifestMismatchError(
                f"RQ3 requires the exact frozen headline protocol; {field} differs"
            )
    expected_id = f"skillrace-issta-main-{protocol.model}-v1"
    if protocol.model not in EXPERIMENT_MODELS or protocol.protocol_id != expected_id:
        raise ManifestMismatchError(
            "RQ3 requires one exact selected dual-model track protocol"
        )
    return protocol


def _validate_stage(stage: pathlib.Path, source: pathlib.Path | None = None) -> dict[str, Any]:
    manifest = _read(stage / "public-stage.json", "public stage manifest")
    if manifest.get("schema") != "skillrace-rq3-public-stage/1":
        raise ManifestMismatchError("public stage schema mismatch")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise ManifestMismatchError("public stage file map is malformed")
    actual = {}
    for path in sorted(stage.rglob("*")):
        if path.is_symlink():
            raise ManifestMismatchError(f"public stage symlink is forbidden: {path}")
        if path.is_file() and path.name != "public-stage.json":
            actual[path.relative_to(stage).as_posix()] = file_hash(path)
    if dict(files) != actual or manifest.get("stage_hash") != canonical_json_hash(actual):
        raise ManifestMismatchError("public stage content hash mismatch")
    if set(path.name for path in stage.iterdir()) != {
        "scenario.md", "base_skill", "campaign", "public-stage.json"
    }:
        raise ManifestMismatchError("public stage has an unexpected top-level entry")
    if source is not None and manifest.get("scenario_id") != source.name:
        raise ManifestMismatchError("public stage scenario identity mismatch")
    return manifest


def _base_manifest_link(
    generation: Mapping[str, Any],
    *,
    base_skill_dir: pathlib.Path,
    public_stage_hash: str,
) -> dict[str, Any]:
    """Project the fully validated base-generation record without dropping provenance."""

    return {
        "schema": generation["schema"],
        "skill_hash": generation["skill_hash"],
        "artifact_hash": canonical_json_hash(generation),
        "package_hash": generation["package_hash"],
        "generation_id": generation["generation_id"],
        "generation_record_hash": file_hash(
            base_skill_dir / ".skillrace" / "base-generation.json"
        ),
        "model_config": generation["model_config"],
        "operation_id": generation["operation_id"],
        "provider_model": generation["provider_model"],
        "provider_response_id_sha256": generation[
            "provider_response_id_sha256"
        ],
        "provider_request_id_sha256": generation.get(
            "provider_request_id_sha256"
        ),
        "billing_status": generation["billing_status"],
        "journal_terminal_event_id": generation["journal_terminal_event_id"],
        "journal_terminal_receipt": generation["journal_terminal_receipt"],
        "journal_terminal_receipt_hash": generation[
            "journal_terminal_receipt_hash"
        ],
        "journal_call_terminal_event_id": generation[
            "journal_call_terminal_event_id"
        ],
        "journal_call_terminal_receipt": generation[
            "journal_call_terminal_receipt"
        ],
        "journal_call_terminal_receipt_hash": generation[
            "journal_call_terminal_receipt_hash"
        ],
        "input_tokens": generation["input_tokens"],
        "output_tokens": generation["output_tokens"],
        "public_stage_hash": public_stage_hash,
        "cost_provider_credits": generation["cost_provider_credits"],
    }


def _materialize_public_work(stage: pathlib.Path, output: pathlib.Path, protocol: CampaignProtocol) -> pathlib.Path:
    work = output / "public-work"
    if not work.exists():
        temporary = pathlib.Path(tempfile.mkdtemp(prefix=".public-work.", dir=output))
        try:
            target = temporary / "work"
            target.mkdir()
            for path in sorted((stage / "base_skill").rglob("*")):
                if path.is_symlink():
                    raise ManifestMismatchError("base skill symlink is forbidden")
                relative = path.relative_to(stage / "base_skill")
                if relative.parts[:1] == (".skillrace",) or not path.is_file():
                    continue
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
            for name in ("properties.json", "applicability.json", "Containerfile.base"):
                shutil.copy2(stage / "campaign" / name, target / name)
            atomic_write_json(target / "protocol.json", protocol.raw)
            hashes = {
                path.relative_to(target).as_posix(): file_hash(path)
                for path in sorted(target.rglob("*"))
                if path.is_file()
            }
            atomic_write_json(
                target / "public-work.json",
                {
                    "schema": "skillrace-rq3-public-work/1",
                    "stage_hash": _read(stage / "public-stage.json", "stage")["stage_hash"],
                    "files": hashes,
                    "work_hash": canonical_json_hash(hashes),
                },
            )
            target.rename(work)
            temporary.rmdir()
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    record = _read(work / "public-work.json", "public work")
    files = {
        path.relative_to(work).as_posix(): file_hash(path)
        for path in sorted(work.rglob("*"))
        if path.is_file() and path.name != "public-work.json"
    }
    if record.get("files") != files or record.get("work_hash") != canonical_json_hash(files):
        raise ManifestMismatchError("public work content hash mismatch")
    return work


def _default_base_builder(request: BaseBuildRequest) -> Mapping[str, Any]:
    repository_root = pathlib.Path(__file__).resolve().parents[1]
    construction_command = [
        "docker", "build", "--progress=plain", "--build-arg",
        "SKILLGEN_BASE_IMAGE=skillrace/skillgen-base:0.73.1-construction",
        "-t", request.construction_image, "-f", str(request.containerfile),
        str(request.context_dir),
    ]
    process = subprocess.run(
        construction_command,
        cwd=request.context_dir,
        env=_clean_env(),
        text=True,
        capture_output=True,
        check=False,
        timeout=3600,
    )
    if process.returncode:
        raise RuntimeError((process.stdout + process.stderr)[-1000:])
    overlay_command = [
        "docker", "build", "--progress=plain", "--build-arg",
        f"SKILL_IMAGE={request.construction_image}", "--build-arg",
        f"TRACK_MODEL={request.model}", "--build-arg",
        f"MODEL_CONFIG=models.yunwu.{request.model}.json", "-t", request.image,
        "-f", str(repository_root / "images/skill-track/Dockerfile.skill-track"),
        str(repository_root / "images/pi-base"),
    ]
    overlay = subprocess.run(
        overlay_command,
        cwd=repository_root,
        env=_clean_env(),
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    if overlay.returncode:
        raise RuntimeError((overlay.stdout + overlay.stderr)[-1000:])

    def inspect(image: str) -> str:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            cwd=request.context_dir,
            env=_clean_env(),
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError((result.stdout + result.stderr)[-1000:])
        identity = result.stdout.strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
            raise RuntimeError(f"Docker returned malformed image identity for {image}")
        return identity

    construction_id = inspect(request.construction_image)
    image_id = inspect(request.image)
    audit_script = (
        "set -euo pipefail; "
        'test "$(pi --version 2>&1)" = "0.73.1"; '
        f'test -f "/skills/{request.skill_name}/SKILL.md"; '
        'test -z "$(git -C /workspace status --porcelain)"; '
        f'MODEL="{request.model}" node -e \'const fs=require("fs"); '
        'const c=JSON.parse(fs.readFileSync("/root/.pi/agent/models.json","utf8")); '
        'const m=c.providers?.yunwu?.models??[]; '
        'if(m.length!==1||m[0].id!==process.env.MODEL)process.exit(2)\''
    )
    inspect = subprocess.run(
        [
            "docker", "run", "--rm", "--network=none", request.image,
            "bash", "-lc", audit_script,
        ],
        cwd=request.context_dir,
        env=_clean_env(),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if inspect.returncode:
        raise RuntimeError((inspect.stdout + inspect.stderr)[-1000:])
    return {
        "construction_image_id": construction_id,
        "image_id": image_id,
        "runtime_audit": "passed-networkless",
    }


def _track_base_image(base_image: str, model: str) -> tuple[str, str]:
    require_model = model if model in EXPERIMENT_MODELS else None
    if require_model is None or ":" not in base_image or "@" in base_image:
        raise ManifestMismatchError("RQ3 base image stem or model is invalid")
    repository, tag = base_image.rsplit(":", 1)
    return (
        f"{repository}:{tag}-{model}",
        f"{repository}:{tag}-construction-{model}",
    )


def _ensure_base_image(
    work: pathlib.Path,
    image: str,
    construction_image: str,
    model: str,
    skill_name: str,
    builder: Callable[[BaseBuildRequest], Mapping[str, Any]],
    record_root: pathlib.Path,
) -> dict[str, Any]:
    record_root.mkdir(parents=True, exist_ok=True)
    start_path = record_root / "start.json"
    receipt_path = record_root / "receipt.json"
    argv = [
        "docker", "build", "--build-arg",
        "SKILLGEN_BASE_IMAGE=skillrace/skillgen-base:0.73.1-construction",
        "-t", construction_image, "-f", str(work / "Containerfile.base"), str(work),
    ]
    start = _launch_record(role="rq3-base-build", cwd=work, argv=argv, roots=[work])
    start["model"] = model
    start["construction_image"] = construction_image
    start["final_image"] = image
    _write_or_verify(start_path, start, "base-build start")
    if receipt_path.exists():
        receipt = _read(receipt_path, "base-build receipt")
        if receipt.get("start_hash") != file_hash(start_path):
            raise ManifestMismatchError("base-build receipt/start mismatch")
        return receipt
    raw = builder(
        BaseBuildRequest(
            image=image,
            construction_image=construction_image,
            model=model,
            skill_name=skill_name,
            context_dir=work,
            containerfile=work / "Containerfile.base",
            launch_path=start_path,
        )
    )
    if (
        not isinstance(raw, Mapping)
        or not isinstance(raw.get("image_id"), str)
        or not isinstance(raw.get("construction_image_id"), str)
    ):
        raise ManifestMismatchError("base builder did not return an immutable image identity")
    receipt = {
        "schema": "skillrace-rq3-base-build/1",
        "start_hash": file_hash(start_path),
        "model": model,
        "image": image,
        "image_id": raw["image_id"],
        "construction_image": construction_image,
        "construction_image_id": raw["construction_image_id"],
        "runtime_audit": raw.get("runtime_audit", "injected-builder-not-audited"),
    }
    atomic_write_json(receipt_path, receipt)
    return receipt


def _campaign_argv(request: CampaignLaunchRequest) -> list[str]:
    return [
        sys.executable, "-m", "skillrace.loop", "--method", request.method,
        "--skill", request.skill_name, "--skill-dir", str(request.skill_dir),
        "--base", request.base_image, "--props", str(request.properties_path),
        "--protocol", str(request.protocol_path), "--wall-clock", str(request.wall_clock),
        "--out", str(request.output_dir),
    ]


def _campaign_confinement(request: CampaignLaunchRequest) -> Phase1Confinement:
    return build_phase1_confinement(
        inner_argv=_campaign_argv(request),
        cwd=request.skill_dir,
        public_roots=(request.skill_dir,),
        output_root=request.output_dir,
        ledger_path=request.output_dir / "provider-ledger" / "cost.jsonl",
        environ=_clean_env(),
        require_docker=True,
    )


def _execute_phase1_confinement(
    launch: Phase1Confinement,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        launch.command,
        env=launch.environment,
        text=True,
        capture_output=True,
        check=False,
    )


def _default_campaign_runner(request: CampaignLaunchRequest) -> pathlib.Path:
    launch = _campaign_confinement(request)
    saved = _read(request.output_dir / "rq3-launch.json", "campaign launch")
    if saved.get("confinement") != launch.record:
        raise ManifestMismatchError("campaign confinement launch identity mismatch")
    process = _execute_phase1_confinement(launch)
    if process.returncode:
        raise RuntimeError((process.stdout + process.stderr)[-1500:])
    return request.output_dir / "campaign.json"


def _revision_child_argv(
    base_skill_dir: pathlib.Path,
    feedback_dir: pathlib.Path,
    output_dir: pathlib.Path,
    model: str,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "skillrace.rq3_pipeline",
        "_revise-public",
        "--base-skill",
        str(base_skill_dir),
        "--feedback-dir",
        str(feedback_dir),
        "--out",
        str(output_dir),
        "--model",
        model,
    ]


def _revision_confinement(
    base_skill_dir: pathlib.Path,
    feedback_dir: pathlib.Path,
    output_dir: pathlib.Path,
    model: str,
) -> Phase1Confinement:
    return build_phase1_confinement(
        inner_argv=_revision_child_argv(
            base_skill_dir, feedback_dir, output_dir, model
        ),
        cwd=base_skill_dir,
        public_roots=(base_skill_dir, feedback_dir),
        output_root=output_dir,
        ledger_path=output_dir / "provider-ledger" / "cost.jsonl",
        environ=_clean_env(),
        require_docker=False,
        role="revision",
    )


def _validated_revision_set(
    *,
    base_skill_dir: pathlib.Path,
    feedback_paths: Mapping[str, pathlib.Path],
    output_dir: pathlib.Path,
    model: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, pathlib.Path]]:
    def forbidden_model_call(*_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
        raise ManifestMismatchError(
            "revision validation attempted an unconfined provider call"
        )

    return revise_feedback_set(
        base_skill_dir=base_skill_dir,
        feedback_paths=feedback_paths,
        out_dir=output_dir,
        chat_fn=nonproduction_chat_fixture(forbidden_model_call),
        model=model,
    )


def _run_revision_phase(
    *,
    base_skill_dir: pathlib.Path,
    feedback_paths: Mapping[str, pathlib.Path],
    output_dir: pathlib.Path,
    revision_chat: Callable[..., Mapping[str, Any]],
    model: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, pathlib.Path]]:
    """Run real revisions in an empty-root child; keep fakes visibly test-only."""

    output_dir.mkdir(parents=True, exist_ok=True)
    feedback_dir = next(iter(feedback_paths.values())).parent.resolve()
    if any(path.parent.resolve() != feedback_dir for path in feedback_paths.values()):
        raise ManifestMismatchError("revision feedback files must share one public directory")
    argv = _revision_child_argv(base_skill_dir, feedback_dir, output_dir, model)
    launch = _launch_record(
        role="rq3-public-revision",
        cwd=base_skill_dir,
        argv=argv,
        roots=[base_skill_dir, feedback_dir, output_dir],
    )
    if revision_chat is chat:
        confinement = _revision_confinement(
            base_skill_dir, feedback_dir, output_dir, model
        )
        launch["confinement"] = confinement.record
        launch["execution_mode"] = "production-confined-child"
    elif is_nonproduction_chat_fixture(revision_chat):
        confinement = None
        launch["confinement"] = {
            "schema": "skillrace-rq3-injected-runner/1",
            "enforced": False,
            "purpose": "test-only revision dependency injection",
        }
        launch["execution_mode"] = "test-only-in-process-fixture"
    else:
        raise ManifestMismatchError(
            "custom revision chat requires the explicit nonproduction fixture boundary"
        )
    launch["base_skill_hash"] = file_hash(base_skill_dir / "SKILL.md")
    launch["feedback_file_hashes"] = {
        producer: file_hash(feedback_paths[producer]) for producer in PRODUCERS
    }
    launch_path = output_dir / "rq3-revision-launch.json"
    _write_or_verify(launch_path, launch, "public revision launch")

    if confinement is None:
        return revise_feedback_set(
            base_skill_dir=base_skill_dir,
            feedback_paths=feedback_paths,
            out_dir=output_dir,
            chat_fn=revision_chat,
            model=model,
        )
    process = _execute_phase1_confinement(confinement)
    if process.returncode:
        raise RuntimeError((process.stdout + process.stderr)[-1500:])
    return _validated_revision_set(
        base_skill_dir=base_skill_dir,
        feedback_paths=feedback_paths,
        output_dir=output_dir,
        model=model,
    )


def _revision_child_main(
    *,
    base_skill_dir: pathlib.Path,
    feedback_dir: pathlib.Path,
    output_dir: pathlib.Path,
    model: str,
) -> None:
    feedback_paths = {
        producer: feedback_dir / f"{producer}.json" for producer in PRODUCERS
    }
    revise_feedback_set(
        base_skill_dir=base_skill_dir,
        feedback_paths=feedback_paths,
        out_dir=output_dir,
        chat_fn=chat,
        model=model,
    )


def _execute_confirmation_request(
    request: ConfirmationRequest,
    *,
    campaign_root: pathlib.Path,
    skill_dir: pathlib.Path,
    model: str,
    wall_clock: int,
) -> Mapping[str, Any]:
    from .loop import check_run, run_agent

    case = pathlib.Path(request.case)
    case = case.resolve() if case.is_absolute() else (campaign_root / case).resolve()
    root = campaign_root.resolve()
    if root not in case.parents:
        raise ValueError("confirmation case escapes its campaign root")
    returncode, _, manifest = run_agent(
        case, request.run_dir, model, wall_clock, skill_dir
    )
    verdicts, _, checker_returncode = check_run(request.run_dir, model)
    cost_path = request.run_dir / "cost.json"
    cost = _read(cost_path, "confirmation cost") if cost_path.is_file() else {}
    status = "completed" if returncode == 0 and checker_returncode == 0 else "error"
    return {
        "status": status,
        "verdicts": verdicts,
        "agent_id": (manifest or {}).get("run_id"),
        "input_tokens": int(cost.get("in", 0) or 0),
        "output_tokens": int(cost.get("out", 0) or 0),
        "cost_provider_credits": float(
            cost.get(
                "cost_provider_credits",
                cost.get("provider_credits", cost.get("price_provider_credits", 0.0)),
            )
            or 0.0
        ),
    }


def _confirmation_child_argv(
    request_path: pathlib.Path, outcome_path: pathlib.Path
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "skillrace.rq3_pipeline",
        "_confirm-public",
        "--request",
        str(request_path),
        "--outcome",
        str(outcome_path),
    ]


def _default_confirmation_executor(
    request: ConfirmationRequest,
    *,
    campaign_root: pathlib.Path,
    skill_dir: pathlib.Path,
    model: str,
    wall_clock: int,
) -> Mapping[str, Any]:
    """Run a confirmation agent/checker in the same empty-root public boundary."""

    campaign_root = campaign_root.resolve()
    skill_dir = skill_dir.resolve()
    case = pathlib.Path(request.case)
    case = case.resolve() if case.is_absolute() else (campaign_root / case).resolve()
    if campaign_root not in case.parents:
        raise ValueError("confirmation case escapes its campaign root")
    run_dir = request.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    request_path = run_dir / "rq3-confirmation-request.json"
    outcome_path = run_dir / "rq3-confirmation-outcome.json"
    payload = {
        "schema": "skillrace-rq3-confirmation-execution/1",
        "request": {
            "cluster_id": request.cluster_id,
            "property_id": request.property_id,
            "failure_signature": request.failure_signature,
            "failure_summary": request.failure_summary,
            "representative_execution_id": request.representative_execution_id,
            "representative_attempt_id": request.representative_attempt_id,
            "representative_candidate_id": request.representative_candidate_id,
            "case": str(case),
            "run_dir": str(run_dir),
        },
        "campaign_root": str(campaign_root),
        "skill_dir": str(skill_dir),
        "model": model,
        "wall_clock": wall_clock,
    }
    _write_or_verify(request_path, payload, "confirmation execution request")
    argv = _confirmation_child_argv(request_path, outcome_path)
    confinement = build_phase1_confinement(
        inner_argv=argv,
        cwd=skill_dir,
        public_roots=(campaign_root, skill_dir),
        output_root=run_dir,
        ledger_path=run_dir / "provider-ledger" / "cost.jsonl",
        environ=_clean_env(),
        require_docker=True,
        role="confirmation",
    )
    launch = _launch_record(
        role="rq3-public-confirmation",
        cwd=skill_dir,
        argv=argv,
        roots=[campaign_root, skill_dir, run_dir],
    )
    launch.update(
        {
            "request_file_hash": file_hash(request_path),
            "confinement": confinement.record,
            "execution_mode": "production-confined-child",
        }
    )
    _write_or_verify(
        run_dir / "rq3-confirmation-launch.json", launch, "confirmation launch"
    )
    if not outcome_path.exists():
        process = _execute_phase1_confinement(confinement)
        if process.returncode:
            raise RuntimeError((process.stdout + process.stderr)[-1500:])
    return _read(outcome_path, "confirmation execution outcome")


def _confirmation_child_main(request_path: pathlib.Path, outcome_path: pathlib.Path) -> None:
    payload = _read(request_path, "confirmation execution request")
    if payload.get("schema") != "skillrace-rq3-confirmation-execution/1":
        raise ManifestMismatchError("confirmation execution request schema mismatch")
    raw = payload.get("request")
    if not isinstance(raw, Mapping):
        raise ManifestMismatchError("confirmation child request is malformed")
    required = {
        "cluster_id",
        "property_id",
        "failure_signature",
        "failure_summary",
        "representative_execution_id",
        "representative_attempt_id",
        "representative_candidate_id",
        "case",
        "run_dir",
    }
    if set(raw) != required:
        raise ManifestMismatchError("confirmation child request fields mismatch")
    request = ConfirmationRequest(
        cluster_id=str(raw["cluster_id"]),
        property_id=str(raw["property_id"]),
        failure_signature=str(raw["failure_signature"]),
        failure_summary=str(raw["failure_summary"]),
        representative_execution_id=str(raw["representative_execution_id"]),
        representative_attempt_id=str(raw["representative_attempt_id"]),
        representative_candidate_id=str(raw["representative_candidate_id"]),
        case=str(raw["case"]),
        run_dir=pathlib.Path(str(raw["run_dir"])),
    )
    result = _execute_confirmation_request(
        request,
        campaign_root=pathlib.Path(str(payload["campaign_root"])),
        skill_dir=pathlib.Path(str(payload["skill_dir"])),
        model=str(payload["model"]),
        wall_clock=int(payload["wall_clock"]),
    )
    atomic_write_json(outcome_path, dict(result))


def _repair_child_argv(request_path: pathlib.Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "skillrace.rq3_pipeline",
        "_repair-public",
        "--request",
        str(request_path),
    ]


def _repair_execution_payload(
    request: FailureRepairRequest,
    evidence: Mapping[str, Any],
    *,
    model: str,
    wall_clock: int,
    repair_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    policy = dict(repair_policy or {
        "timeout_seconds": 120,
        "max_output_tokens": 4000,
        "temperature": 0.0,
        "reasoning": True,
        "backend": "direct",
    })
    return {
        "schema": "skillrace-rq3-repair-execution/1",
        "request": request.identity(),
        "paths": {
            "case_dir": str(request.case_dir.resolve()),
            "original_skill_dir": str(request.original_skill_dir.resolve()),
            "run_dir": str(request.run_dir.resolve()),
            "output_dir": str(request.output_dir.resolve()),
        },
        "model": model,
        "wall_clock": wall_clock,
        "repair_policy": policy,
        "evidence_hash": evidence.get("evidence_hash"),
    }


def _default_repair_job_runner(
    request: FailureRepairRequest,
    evidence: Mapping[str, Any],
    *,
    model: str,
    wall_clock: int,
    repair_policy: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Run one entire patch/exact-replay job in an empty-root public child."""

    output = request.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    evidence_path = output / "evidence.json"
    if _read(evidence_path, "repair evidence") != dict(evidence):
        raise ManifestMismatchError("repair evidence differs from frozen job evidence")
    request_path = output / "rq3-repair-request.json"
    payload = _repair_execution_payload(
        request,
        evidence,
        model=model,
        wall_clock=wall_clock,
        repair_policy=repair_policy,
    )
    _write_or_verify(request_path, payload, "repair execution request")
    argv = _repair_child_argv(request_path)
    confinement = build_phase1_confinement(
        inner_argv=argv,
        cwd=request.original_skill_dir,
        public_roots=(request.original_skill_dir, request.case_dir),
        output_root=output,
        ledger_path=output / "provider-ledger" / "cost.jsonl",
        environ=_clean_env(),
        require_docker=True,
        role="repair",
    )
    launch = _launch_record(
        role="rq3-public-repair",
        cwd=request.original_skill_dir,
        argv=argv,
        roots=[request.original_skill_dir, request.case_dir, output],
    )
    launch.update(
        {
            "schema": "skillrace-rq3-repair-launch/1",
            "execution_mode": "production-confined-child",
            "repair_id": request.repair_id,
            "confinement": confinement.record,
        }
    )
    _write_or_verify(output / "rq3-repair-launch.json", launch, "repair launch")
    process = _execute_phase1_confinement(confinement)
    if process.returncode != 0:
        detail = (process.stdout + process.stderr).strip()[-1000:]
        raise RuntimeError(detail or f"repair child exited {process.returncode}")
    result = _read(output / "repair.json", "repair result")
    if (
        result.get("schema") != "skillrace-failure-repair-result/1"
        or result.get("repair_id") != request.repair_id
    ):
        raise ManifestMismatchError("repair child returned an unrelated result")
    return result


def _repair_child_main(request_path: pathlib.Path) -> None:
    payload = _read(request_path, "repair execution request")
    if payload.get("schema") != "skillrace-rq3-repair-execution/1":
        raise ManifestMismatchError("repair execution request schema mismatch")
    identity = payload.get("request")
    paths = payload.get("paths")
    if not isinstance(identity, Mapping) or not isinstance(paths, Mapping):
        raise ManifestMismatchError("repair child request is malformed")
    required_identity = {
        "method",
        "skill_name",
        "execution_id",
        "attempt_id",
        "candidate_id",
        "original_skill_hash",
        "failed_property_ids",
        "failure_signatures",
    }
    if (
        identity.get("schema") != "skillrace-failure-repair-request/1"
        or not required_identity.issubset(identity)
        or set(paths) != {"case_dir", "original_skill_dir", "run_dir", "output_dir"}
    ):
        raise ManifestMismatchError("repair child request fields mismatch")
    output = pathlib.Path(str(paths["output_dir"])).resolve()
    if request_path.resolve().parent != output:
        raise ManifestMismatchError("repair child request escapes its output directory")
    request = FailureRepairRequest(
        method=str(identity["method"]),
        skill_name=str(identity["skill_name"]),
        execution_id=str(identity["execution_id"]),
        attempt_id=str(identity["attempt_id"]),
        candidate_id=str(identity["candidate_id"]),
        case_dir=pathlib.Path(str(paths["case_dir"])).resolve(),
        original_skill_dir=pathlib.Path(str(paths["original_skill_dir"])).resolve(),
        original_skill_hash=str(identity["original_skill_hash"]),
        failed_property_ids=tuple(identity["failed_property_ids"]),
        failure_signatures=tuple(identity["failure_signatures"]),
        run_dir=pathlib.Path(str(paths["run_dir"])).resolve(),
        output_dir=output,
        repair_id=canonical_json_hash(dict(identity))[:24],
    )
    if request.identity() != dict(identity):
        raise ManifestMismatchError("repair child request identity mismatch")
    evidence = _read(output / "evidence.json", "repair evidence")
    if evidence.get("evidence_hash") != payload.get("evidence_hash"):
        raise ManifestMismatchError("repair child evidence hash mismatch")
    model = payload.get("model")
    wall_clock = payload.get("wall_clock")
    repair_policy = payload.get("repair_policy")
    if (
        not isinstance(repair_policy, Mapping)
        or set(repair_policy)
        != {
            "backend",
            "timeout_seconds",
            "max_output_tokens",
            "temperature",
            "reasoning",
        }
    ):
        raise ManifestMismatchError("repair child policy is missing")
    backend_name = repair_policy.get("backend")
    if backend_name == "direct":
        from .direct_patcher import make_direct_patcher

        patcher = make_direct_patcher(
            model=str(model),
            timeout_seconds=int(repair_policy["timeout_seconds"]),
            max_tokens=int(repair_policy["max_output_tokens"]),
            temperature=float(repair_policy["temperature"]),
            reasoning=bool(repair_policy["reasoning"]),
        )
    elif backend_name == "pi":
        from .pi_patcher import make_pi_patcher

        patcher = make_pi_patcher(
            model=str(model),
            timeout_seconds=int(repair_policy["timeout_seconds"]),
        )
    else:
        raise ManifestMismatchError("repair child backend is invalid")
    repair_failed_execution(
        request,
        evidence,
        patcher=patcher,
        executor=make_replay_executor(model=model, wall_clock=wall_clock),
    )


def _campaign_launch(
    request: CampaignLaunchRequest,
    runner: Callable[[CampaignLaunchRequest], str | pathlib.Path],
) -> pathlib.Path:
    request.output_dir.mkdir(parents=True, exist_ok=True)
    argv = _campaign_argv(request)
    launch = _launch_record(
        role=f"rq3-campaign-{request.method}",
        cwd=request.skill_dir,
        argv=argv,
        roots=[request.skill_dir, request.output_dir],
    )
    launch.update(
        {
            "protocol_hash": request.protocol_hash,
            "base_skill_hash": request.base_skill_hash,
            "base_package_hash": request.base_package_hash,
            "public_stage_hash": request.public_stage_hash,
            "confinement": (
                _campaign_confinement(request).record
                if runner is _default_campaign_runner
                else {
                    "schema": "skillrace-rq3-injected-runner/1",
                    "enforced": False,
                    "purpose": "test-only dependency injection",
                }
            ),
        }
    )
    _write_or_verify(request.output_dir / "rq3-launch.json", launch, "campaign launch")
    expected = request.output_dir / "campaign.json"
    if not expected.exists():
        returned = pathlib.Path(runner(request)).resolve()
        if returned != expected.resolve():
            raise ManifestMismatchError("campaign runner returned an arbitrary artifact path")
    state = _read(expected, "campaign")
    prepare_campaign_input_record(
        request.output_dir,
        method=request.method,
        protocol_hash=request.protocol_hash,
        base_skill_hash=request.base_skill_hash,
        base_package_hash=request.base_package_hash,
        public_stage_hash=request.public_stage_hash,
        output_identity=state.get("output_identity"),
    )
    derive_campaign_cost_record(expected)
    return expected


def _public_roots(output: pathlib.Path) -> list[pathlib.Path]:
    return [
        output / "public-stage",
        output / "public-work",
        output / "base-build",
        output / "campaigns",
        output / "confirmations",
        output / "repairs",
        output / "feedback",
        output / "revisions",
        output / "public-phase-complete.json",
    ]


def _public_launch_link(path: pathlib.Path, label: str) -> dict[str, Any]:
    launch = _read(path, label)
    confinement = launch.get("confinement")
    if not isinstance(confinement, Mapping):
        raise ManifestMismatchError(f"{label} confinement record is missing")
    if confinement.get("enforced") is True:
        try:
            validate_phase1_confinement_record(confinement)
        except RuntimeError as error:
            raise ManifestMismatchError(f"{label} confinement is invalid: {error}") from error
        policy_hash = confinement["policy_hash"]
        mode = "production-confined"
    elif confinement == {
        "schema": "skillrace-rq3-injected-runner/1",
        "enforced": False,
        "purpose": "test-only dependency injection",
    } or confinement == {
        "schema": "skillrace-rq3-injected-runner/1",
        "enforced": False,
        "purpose": "test-only revision dependency injection",
    }:
        policy_hash = None
        mode = "test-only-injected"
    else:
        raise ManifestMismatchError(f"{label} has an unknown confinement boundary")
    return {
        "launch_file_hash": file_hash(path),
        "confinement_enforced": confinement.get("enforced"),
        "confinement_policy_hash": policy_hash,
        "mode": mode,
    }


def _confirmation_boundary_link(
    output: pathlib.Path, producer: str, campaign_hash: str
) -> dict[str, Any]:
    root = output / "confirmations" / producer
    policy_path = root / "rq3-confirmation-policy.json"
    policy = _read(policy_path, f"{producer} confirmation policy")
    if policy != {
        "schema": "skillrace-rq3-confirmation-policy/1",
        "method": producer,
        "source_campaign_hash": campaign_hash,
        "production_confinement_required": policy.get(
            "production_confinement_required"
        ),
        "execution_mode": policy.get("execution_mode"),
    }:
        raise ManifestMismatchError(f"{producer} confirmation policy identity mismatch")
    required = policy["production_confinement_required"]
    expected_mode = (
        "production-confined-per-cluster"
        if required is True
        else "test-only-injected-executor"
    )
    if not isinstance(required, bool) or policy["execution_mode"] != expected_mode:
        raise ManifestMismatchError(f"{producer} confirmation policy mode mismatch")
    ledger = _read(root / "confirmation.json", f"{producer} confirmation ledger")
    clusters = ledger.get("clusters")
    if not isinstance(clusters, list):
        raise ManifestMismatchError(f"{producer} confirmation cluster list is malformed")
    launch_links: dict[str, dict[str, Any]] = {}
    for cluster in clusters:
        cluster_id = cluster.get("cluster_id") if isinstance(cluster, Mapping) else None
        if not isinstance(cluster_id, str):
            raise ManifestMismatchError(f"{producer} confirmation cluster ID is malformed")
        launch_path = root / "clusters" / cluster_id / "agent" / "rq3-confirmation-launch.json"
        if required:
            launch_links[cluster_id] = _public_launch_link(
                launch_path, f"{producer}/{cluster_id} confirmation launch"
            )
            run_dir = launch_path.parent
            request_path = run_dir / "rq3-confirmation-request.json"
            outcome_path = run_dir / "rq3-confirmation-outcome.json"
            request_payload = _read(
                request_path, f"{producer}/{cluster_id} confirmation request"
            )
            if (
                request_payload.get("schema")
                != "skillrace-rq3-confirmation-execution/1"
                or not isinstance(request_payload.get("campaign_root"), str)
                or not isinstance(request_payload.get("skill_dir"), str)
            ):
                raise ManifestMismatchError(
                    f"{producer}/{cluster_id} confirmation request identity mismatch"
                )
            expected_confinement = build_phase1_confinement(
                inner_argv=_confirmation_child_argv(request_path, outcome_path),
                cwd=pathlib.Path(str(request_payload["skill_dir"])),
                public_roots=(
                    pathlib.Path(str(request_payload["campaign_root"])),
                    pathlib.Path(str(request_payload["skill_dir"])),
                ),
                output_root=run_dir,
                ledger_path=run_dir / "provider-ledger" / "cost.jsonl",
                environ=_clean_env(),
                require_docker=True,
                role="confirmation",
            )
            saved_launch = _read(
                launch_path, f"{producer}/{cluster_id} confirmation launch"
            )
            if saved_launch.get("confinement") != expected_confinement.record:
                raise ManifestMismatchError(
                    f"{producer}/{cluster_id} confirmation confinement resume mismatch"
                )
        elif launch_path.exists():
            raise ManifestMismatchError(
                f"test-only confirmation unexpectedly claims a production launch: {cluster_id}"
            )
    return {
        "policy_file_hash": file_hash(policy_path),
        "production_confinement_required": required,
        "execution_mode": expected_mode,
        "cluster_launches": launch_links,
    }


def _repair_boundary_link(
    output: pathlib.Path, producer: str, campaign_hash: str
) -> dict[str, Any]:
    root = output / "repairs" / producer
    policy_path = root / "rq3-repair-policy.json"
    policy = _read(policy_path, f"{producer} repair policy")
    required = policy.get("production_confinement_required")
    expected_mode = (
        "production-confined-per-failure"
        if required is True
        else "test-only-injected-job-runner"
    )
    if policy != {
        "schema": "skillrace-rq3-repair-policy/1",
        "method": producer,
        "source_campaign_hash": campaign_hash,
        "production_confinement_required": required,
        "execution_mode": policy.get("execution_mode"),
        "backend": policy.get("backend"),
        "timeout_seconds": policy.get("timeout_seconds"),
    }:
        raise ManifestMismatchError(f"{producer} repair policy identity mismatch")
    if (
        not isinstance(required, bool)
        or policy.get("execution_mode") != expected_mode
        or policy.get("backend") not in {"direct", "pi"}
        or isinstance(policy.get("timeout_seconds"), bool)
        or not isinstance(policy.get("timeout_seconds"), int)
        or not 1 <= policy["timeout_seconds"] <= 600
    ):
        raise ManifestMismatchError(f"{producer} repair policy mode mismatch")
    ledger = validate_repair_ledger(root / "repairs.json")
    if (
        ledger.get("method") != producer
        or ledger.get("source_campaign_hash") != campaign_hash
    ):
        raise ManifestMismatchError(f"{producer} repair ledger identity mismatch")
    launches: dict[str, dict[str, Any]] = {}
    for link in ledger["repairs"]:
        repair_id = link["repair_id"]
        repair_root = root / repair_id
        launch_path = repair_root / "rq3-repair-launch.json"
        if required:
            launches[repair_id] = _public_launch_link(
                launch_path, f"{producer}/{repair_id} repair launch"
            )
            payload = _read(
                repair_root / "rq3-repair-request.json",
                f"{producer}/{repair_id} repair request",
            )
            paths = payload.get("paths")
            if (
                payload.get("schema") != "skillrace-rq3-repair-execution/1"
                or not isinstance(paths, Mapping)
                or set(paths)
                != {"case_dir", "original_skill_dir", "run_dir", "output_dir"}
                or pathlib.Path(str(paths["output_dir"])).resolve() != repair_root.resolve()
            ):
                raise ManifestMismatchError(
                    f"{producer}/{repair_id} repair request identity mismatch"
                )
            expected = build_phase1_confinement(
                inner_argv=_repair_child_argv(
                    repair_root / "rq3-repair-request.json"
                ),
                cwd=pathlib.Path(str(paths["original_skill_dir"])),
                public_roots=(
                    pathlib.Path(str(paths["original_skill_dir"])),
                    pathlib.Path(str(paths["case_dir"])),
                ),
                output_root=repair_root,
                ledger_path=repair_root / "provider-ledger" / "cost.jsonl",
                environ=_clean_env(),
                require_docker=True,
                role="repair",
            )
            saved = _read(launch_path, f"{producer}/{repair_id} repair launch")
            if saved.get("confinement") != expected.record:
                raise ManifestMismatchError(
                    f"{producer}/{repair_id} repair confinement resume mismatch"
                )
        elif launch_path.exists():
            raise ManifestMismatchError(
                f"test-only repair unexpectedly claims a production launch: {repair_id}"
            )
    return {
        "policy_file_hash": file_hash(policy_path),
        "ledger_file_hash": file_hash(root / "repairs.json"),
        "production_confinement_required": required,
        "execution_mode": expected_mode,
        "backend": policy["backend"],
        "timeout_seconds": policy["timeout_seconds"],
        "repair_launches": launches,
    }


def _public_phase_record(
    *,
    output: pathlib.Path,
    scenario_id: str,
    protocol_hash: str,
    public_stage_hash: str,
    base_skill_hash: str,
    campaigns: Mapping[str, Mapping[str, Any]],
    repairs: Mapping[str, Mapping[str, Any]],
    envelopes: Mapping[str, Mapping[str, Any]],
    revisions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    revision_models = {
        record.get("model_config", {}).get("model") for record in revisions.values()
    }
    if len(revision_models) != 1:
        raise ManifestMismatchError("public revisions do not share one track model")
    track_model = next(iter(revision_models))
    campaign_links = {
        producer: {
            **_public_launch_link(
                output / "campaigns" / producer / "rq3-launch.json",
                f"{producer} campaign launch",
            ),
            "campaign_artifact_hash": campaigns[producer]["artifact_hash"],
            "confirmation_file_hash": file_hash(
                output / "confirmations" / producer / "confirmation.json"
            ),
            "confirmation_boundary": _confirmation_boundary_link(
                output, producer, campaigns[producer]["artifact_hash"]
            ),
            "repair_boundary": _repair_boundary_link(
                output, producer, campaigns[producer]["artifact_hash"]
            ),
        }
        for producer in PRODUCERS
    }
    revision_link = _public_launch_link(
        output / "revisions" / "rq3-revision-launch.json",
        "public revision launch",
    )
    saved_revision_launch = _read(
        output / "revisions" / "rq3-revision-launch.json",
        "public revision launch",
    )
    if revision_link["confinement_enforced"]:
        expected_revision_confinement = _revision_confinement(
            output / "public-stage" / "base_skill",
            output / "feedback",
            output / "revisions",
            track_model,
        )
        if (
            saved_revision_launch.get("confinement")
            != expected_revision_confinement.record
        ):
            raise ManifestMismatchError(
                "public revision confinement resume identity mismatch"
            )
    production_enforced = revision_link["confinement_enforced"] and all(
        link["confinement_enforced"]
        and link["confirmation_boundary"]["production_confinement_required"]
        and link["repair_boundary"]["production_confinement_required"]
        for link in campaign_links.values()
    )
    return {
        "schema": "skillrace-rq3-public-phase-complete/1",
        "scenario_id": scenario_id,
        "protocol_hash": protocol_hash,
        "public_stage_hash": public_stage_hash,
        "base_skill_hash": base_skill_hash,
        "phase_sequence": [
            "public-campaigns",
            "public-per-failure-repairs",
            "public-confirmations",
            "feedback-projection",
            "blind-revisions",
            "public-sentinel-audit",
            "hidden-evaluation",
        ],
        "hidden_evaluation_status": "not-started-at-public-barrier",
        "hidden_material_included": False,
        "production_testing_and_revision_confinement_enforced": bool(
            production_enforced
        ),
        "campaigns": campaign_links,
        "repair_ledger_hashes": {
            producer: canonical_json_hash(repairs[producer]) for producer in PRODUCERS
        },
        "feedback_envelope_hashes": {
            producer: envelopes[producer]["artifact_hash"] for producer in PRODUCERS
        },
        "revision_launch": revision_link,
        "revision_input_contract": {
            "base_skill_hash": base_skill_hash,
            "envelope_hashes": {
                producer: envelopes[producer]["artifact_hash"]
                for producer in PRODUCERS
            },
            "hidden_path_or_content": "absent",
        },
        "revision_artifact_hashes": {
            producer: revisions[producer]["artifact_hash"] for producer in PRODUCERS
        },
    }


def _write_or_validate_public_barrier(
    *,
    output: pathlib.Path,
    scenario_id: str,
    protocol_hash: str,
    public_stage_hash: str,
    base_skill_hash: str,
    campaigns: Mapping[str, Mapping[str, Any]],
    repairs: Mapping[str, Mapping[str, Any]],
    envelopes: Mapping[str, Mapping[str, Any]],
    revisions: Mapping[str, Mapping[str, Any]],
    create: bool,
) -> dict[str, Any]:
    expected = _public_phase_record(
        output=output,
        scenario_id=scenario_id,
        protocol_hash=protocol_hash,
        public_stage_hash=public_stage_hash,
        base_skill_hash=base_skill_hash,
        campaigns=campaigns,
        repairs=repairs,
        envelopes=envelopes,
        revisions=revisions,
    )
    path = output / "public-phase-complete.json"
    if create:
        _write_or_verify(path, expected, "public phase barrier")
    elif _read(path, "public phase barrier") != expected:
        raise ManifestMismatchError("public phase barrier identity mismatch")
    return expected


def run_rq3_scenario(
    *,
    scenario_dir: str | pathlib.Path,
    out_dir: str | pathlib.Path,
    protocol_path: str | pathlib.Path,
    scenarios_root: str | pathlib.Path | None = None,
    replication: int = 1,
    wall_clock: int = 1200,
    feedback_max_bytes: int = FROZEN_FEEDBACK_MAX_BYTES,
    campaign_runner: Callable[[CampaignLaunchRequest], str | pathlib.Path] = _default_campaign_runner,
    base_builder: Callable[[BaseBuildRequest], Mapping[str, Any]] = _default_base_builder,
    revision_chat: Callable[..., Mapping[str, Any]] = chat,
    confirmation_executor: Callable[[ConfirmationRequest], Mapping[str, Any]] | None = None,
    repair_job_runner: Callable[
        [FailureRepairRequest, Mapping[str, Any]], Mapping[str, Any]
    ]
    | None = None,
    hidden_executor: Callable[[HiddenExecutionRequest], Mapping[str, Any]] = execute_hidden_request,
) -> dict[str, Any]:
    """Run stage -> campaigns -> repairs/confirmation -> revision -> hidden exam."""

    source = pathlib.Path(scenario_dir).resolve()
    root = pathlib.Path(scenarios_root).resolve() if scenarios_root else source.parent
    if source.parent != root or source.name in {"", ".", ".."}:
        raise ManifestMismatchError("scenario must be one exact direct child of scenarios_root")
    output = pathlib.Path(out_dir).resolve()
    if output == source or source in output.parents or output in source.parents:
        raise ManifestMismatchError("RQ3 output must not overlap the source scenario")
    if feedback_max_bytes != FROZEN_FEEDBACK_MAX_BYTES:
        raise ManifestMismatchError(
            f"RQ3 feedback budget must be exactly {FROZEN_FEEDBACK_MAX_BYTES} bytes"
        )
    scenario = load_scenario(source)
    config = load_public_campaign_config(source)
    protocol = _validate_frozen_protocol(pathlib.Path(protocol_path))
    output.mkdir(parents=True, exist_ok=True)
    stage = output / "public-stage"
    if not stage.exists():
        stage_public_scenario(source, stage)
    stage_record = _validate_stage(stage, source)
    generation = validate_base_generation(
        stage / "base_skill", expected_model=protocol.model
    )
    if generation.get("scenario_id") != scenario.scenario_id:
        raise ManifestMismatchError("base-generation scenario identity mismatch")
    base_skill_hash = file_hash(stage / "base_skill" / "SKILL.md")
    if base_skill_hash != scenario.base_skill_sha256:
        raise ManifestMismatchError("generated base skill differs from scenario contract")
    base_package_hash = package_hash(stage / "base_skill")
    work = _materialize_public_work(stage, output, protocol)
    if file_hash(work / "SKILL.md") != base_skill_hash:
        raise ManifestMismatchError("derived public campaign skill hash mismatch")
    base_image, construction_image = _track_base_image(
        config["base_image"], protocol.model
    )
    _ensure_base_image(
        work,
        base_image,
        construction_image,
        protocol.model,
        scenario.scenario_id,
        base_builder,
        output / "base-build",
    )

    campaign_paths: dict[str, pathlib.Path] = {}
    campaign_records: dict[str, dict[str, Any]] = {}
    repair_records: dict[str, dict[str, Any]] = {}
    confirmation_paths: dict[str, pathlib.Path] = {}
    for method in PRODUCERS:
        request = CampaignLaunchRequest(
            method=method,
            skill_name=scenario.scenario_id,
            skill_dir=work,
            base_image=base_image,
            properties_path=work / "properties.json",
            protocol_path=work / "protocol.json",
            output_dir=output / "campaigns" / method,
            wall_clock=wall_clock,
            protocol_hash=protocol.hash,
            base_skill_hash=base_skill_hash,
            base_package_hash=base_package_hash,
            public_stage_hash=stage_record["stage_hash"],
        )
        campaign_path = _campaign_launch(request, campaign_runner)
        record = validate_campaign_artifact(
            campaign_path,
            expected_method=method,
            expected_protocol_hash=protocol.hash,
            expected_base_skill_hash=base_skill_hash,
            expected_model=protocol.model,
        )
        campaign_paths[method] = campaign_path
        campaign_records[method] = record
        campaign = _read(campaign_path, "campaign")
        repair_root = output / "repairs" / method
        repair_root.mkdir(parents=True, exist_ok=True)
        _write_or_verify(
            repair_root / "rq3-repair-policy.json",
            {
                "schema": "skillrace-rq3-repair-policy/1",
                "method": method,
                "source_campaign_hash": campaign_records[method]["artifact_hash"],
                "production_confinement_required": repair_job_runner is None,
                "execution_mode": (
                    "production-confined-per-failure"
                    if repair_job_runner is None
                    else "test-only-injected-job-runner"
                ),
                "backend": protocol.repair.backend_for(method),
                "timeout_seconds": protocol.repair.timeout_seconds,
            },
            "repair confinement policy",
        )
        job_runner = repair_job_runner
        if job_runner is None:
            repair_policy = {
                "backend": protocol.repair.backend_for(method),
                "timeout_seconds": protocol.repair.timeout_seconds,
                "max_output_tokens": protocol.repair.max_output_tokens,
                "temperature": protocol.repair.temperature,
                "reasoning": protocol.repair.reasoning,
            }
            job_runner = lambda request, evidence: _default_repair_job_runner(
                request,
                evidence,
                model=protocol.model,
                wall_clock=wall_clock,
                repair_policy=repair_policy,
            )
        repair_records[method] = repair_campaign_failures(
            campaign,
            skill_name=scenario.scenario_id,
            original_skill_dir=stage / "base_skill",
            campaign_root=campaign_path.parent,
            output_root=repair_root,
            job_runner=job_runner,
            evidence_max_bytes=FROZEN_FEEDBACK_MAX_BYTES,
        )
        executor = confirmation_executor
        if executor is None:
            executor = lambda value, root=campaign_path.parent: _default_confirmation_executor(
                value,
                campaign_root=root,
                skill_dir=work,
                model=protocol.model,
                wall_clock=wall_clock,
            )
        confirmation_root = output / "confirmations" / method
        confirmation_root.mkdir(parents=True, exist_ok=True)
        _write_or_verify(
            confirmation_root / "rq3-confirmation-policy.json",
            {
                "schema": "skillrace-rq3-confirmation-policy/1",
                "method": method,
                "source_campaign_hash": campaign_records[method]["artifact_hash"],
                "production_confinement_required": confirmation_executor is None,
                "execution_mode": (
                    "production-confined-per-cluster"
                    if confirmation_executor is None
                    else "test-only-injected-executor"
                ),
            },
            "confirmation confinement policy",
        )
        confirm_campaign_findings(
            campaign,
            confirmation_root,
            executor=executor,
            campaign_root=campaign_path.parent,
        )
        confirmation_paths[method] = output / "confirmations" / method / "confirmation.json"

    feedback_paths, envelope_records, projected_campaign_records = project_feedback_set(
        campaign_paths=campaign_paths,
        confirmation_paths=confirmation_paths,
        out_dir=output / "feedback",
        expected_protocol_hash=protocol.hash,
        expected_base_skill_hash=base_skill_hash,
        expected_model=protocol.model,
        max_bytes=feedback_max_bytes,
    )
    if projected_campaign_records != campaign_records:
        raise ManifestMismatchError("campaign records changed between confirmation and projection")
    revision_records, skills = _run_revision_phase(
        base_skill_dir=stage / "base_skill",
        feedback_paths=feedback_paths,
        output_dir=output / "revisions",
        revision_chat=revision_chat,
        model=protocol.model,
    )
    _write_or_validate_public_barrier(
        output=output,
        scenario_id=scenario.scenario_id,
        protocol_hash=protocol.hash,
        public_stage_hash=stage_record["stage_hash"],
        base_skill_hash=base_skill_hash,
        campaigns=campaign_records,
        repairs=repair_records,
        envelopes=envelope_records,
        revisions=revision_records,
        create=True,
    )
    hidden_root = scenario.hidden_tests_dir
    public_roots = _public_roots(output)
    assert_no_hidden_material(hidden_root, public_roots)
    base_record = _base_manifest_link(
        generation,
        base_skill_dir=stage / "base_skill",
        public_stage_hash=stage_record["stage_hash"],
    )
    manifest = evaluate_hidden_scenario(
        scenario_dir=source,
        out_dir=output,
        protocol_hash=protocol.hash,
        replication=replication,
        base_skill=base_record,
        campaigns=campaign_records,
        repairs=repair_records,
        envelopes=envelope_records,
        revisions=revision_records,
        skills_by_condition=skills,
        model_config={"model": protocol.model, "wall_clock": wall_clock},
        public_artifact_roots=public_roots,
        executor=hidden_executor,
    )
    verify_rq3_artifacts(output, scenario_dir=source)
    return load_rq3_manifest(output / "rq3-manifest.json", expected_protocol_hash=protocol.hash)


def verify_rq3_artifacts(
    out_dir: str | pathlib.Path, *, scenario_dir: str | pathlib.Path
) -> dict[str, Any]:
    """Recursively verify every linked public, revision, and hidden terminal artifact."""

    output = pathlib.Path(out_dir).resolve()
    source = pathlib.Path(scenario_dir).resolve()
    manifest = load_rq3_manifest(output / "rq3-manifest.json")
    stage_record = _validate_stage(output / "public-stage", source)
    generation = validate_base_generation(
        output / "public-stage" / "base_skill",
        expected_model=manifest["model_config"]["model"],
    )
    base_hash = file_hash(output / "public-stage" / "base_skill" / "SKILL.md")
    expected_base_link = _base_manifest_link(
        generation,
        base_skill_dir=output / "public-stage" / "base_skill",
        public_stage_hash=stage_record["stage_hash"],
    )
    if manifest.get("base_skill") != expected_base_link:
        raise ManifestMismatchError("manifest/base-generation provenance link mismatch")
    verified_campaigns: dict[str, dict[str, Any]] = {}
    verified_repairs: dict[str, dict[str, Any]] = {}
    verified_envelopes: dict[str, dict[str, Any]] = {}
    verified_revisions: dict[str, dict[str, Any]] = {}
    for method in PRODUCERS:
        campaign = validate_campaign_artifact(
            output / "campaigns" / method / "campaign.json",
            expected_method=method,
            expected_protocol_hash=manifest["protocol_hash"],
            expected_base_skill_hash=base_hash,
            expected_model=manifest["model_config"]["model"],
        )
        if manifest["campaigns"][method] != campaign:
            raise ManifestMismatchError(f"manifest/{method} campaign link mismatch")
        verified_campaigns[method] = campaign
        repair = validate_repair_ledger(
            output / "repairs" / method / "repairs.json"
        )
        campaign_state = _read(
            output / "campaigns" / method / "campaign.json",
            f"{method} campaign state",
        )
        expected_repairs = select_failure_repairs(
            campaign_state,
            skill_name=manifest["scenario_id"],
            original_skill_dir=output / "public-stage" / "base_skill",
            campaign_root=output / "campaigns" / method,
            output_root=output / "repairs" / method,
            phase="public",
        )
        if [row["repair_id"] for row in repair["repairs"]] != [
            request.repair_id for request in expected_repairs
        ]:
            raise ManifestMismatchError(
                f"{method} repairs are not exactly one per failed public execution"
            )
        if manifest.get("repairs", {}).get(method) != repair:
            raise ManifestMismatchError(f"manifest/{method} repair link mismatch")
        verified_repairs[method] = repair
        confirmation = validate_confirmation_ledger(
            output / "confirmations" / method / "confirmation.json",
            campaign_root=output / "campaigns" / method,
        )
        envelope = feedback_record_from_file(
            output / "feedback" / f"{method}.json",
            expected_campaign_hash=campaign["artifact_hash"],
            expected_confirmation_hash=canonical_json_hash(confirmation),
        )
        if manifest["feedback_envelopes"][method] != envelope:
            raise ManifestMismatchError(f"manifest/{method} envelope link mismatch")
        verified_envelopes[method] = envelope
        revision, _ = revision_record_from_artifact(
            output / "revisions" / method,
            expected_base_skill_hash=base_hash,
            expected_envelope_hash=envelope["artifact_hash"],
            expected_model=manifest["model_config"]["model"],
        )
        receipt_path = output / "revisions" / f"{method}.receipt.json"
        start_path = output / "revisions" / f"{method}.start.json"
        receipt = _read(receipt_path, "revision receipt")
        if (
            receipt.get("start_hash") != file_hash(start_path)
            or receipt.get("revision_record_hash") != revision["artifact_hash"]
        ):
            raise ManifestMismatchError(f"{method} revision receipt mismatch")
        revision["start_hash"] = file_hash(start_path)
        revision["receipt_hash"] = file_hash(receipt_path)
        if manifest["revisions"][method] != revision:
            raise ManifestMismatchError(f"manifest/{method} revision link mismatch")
        verified_revisions[method] = revision

    _write_or_validate_public_barrier(
        output=output,
        scenario_id=manifest["scenario_id"],
        protocol_hash=manifest["protocol_hash"],
        public_stage_hash=stage_record["stage_hash"],
        base_skill_hash=base_hash,
        campaigns=verified_campaigns,
        repairs=verified_repairs,
        envelopes=verified_envelopes,
        revisions=verified_revisions,
        create=False,
    )

    verified_manifest = verify_rq3_evaluation_artifacts(
        output / "rq3-manifest.json",
        scenario_dir=source,
        require_complete=True,
    )
    scenario = load_scenario(source)
    if any((output / "evaluations").rglob("repairs.json")):
        raise ManifestMismatchError("hidden evaluation must not contain repair jobs")
    assert_no_hidden_material(scenario.hidden_tests_dir, _public_roots(output))
    return verified_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Run or verify the complete lean RQ3 pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--scenario", required=True)
    run.add_argument("--scenarios-root")
    run.add_argument("--protocol", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--replication", type=int, default=1)
    run.add_argument("--wall-clock", type=int, default=1200)
    verify = commands.add_parser("verify")
    verify.add_argument("--scenario", required=True)
    verify.add_argument("--out", required=True)
    revise = commands.add_parser("_revise-public", help=argparse.SUPPRESS)
    revise.add_argument("--base-skill", required=True)
    revise.add_argument("--feedback-dir", required=True)
    revise.add_argument("--out", required=True)
    revise.add_argument("--model", required=True, choices=EXPERIMENT_MODELS)
    confirm = commands.add_parser("_confirm-public", help=argparse.SUPPRESS)
    confirm.add_argument("--request", required=True)
    confirm.add_argument("--outcome", required=True)
    repair = commands.add_parser("_repair-public", help=argparse.SUPPRESS)
    repair.add_argument("--request", required=True)
    args = parser.parse_args()
    if args.command == "run":
        value = run_rq3_scenario(
            scenario_dir=args.scenario,
            scenarios_root=args.scenarios_root,
            protocol_path=args.protocol,
            out_dir=args.out,
            replication=args.replication,
            wall_clock=args.wall_clock,
        )
    elif args.command == "verify":
        value = verify_rq3_artifacts(args.out, scenario_dir=args.scenario)
    elif args.command == "_revise-public":
        _revision_child_main(
            base_skill_dir=pathlib.Path(args.base_skill),
            feedback_dir=pathlib.Path(args.feedback_dir),
            output_dir=pathlib.Path(args.out),
            model=args.model,
        )
        return
    elif args.command == "_confirm-public":
        _confirmation_child_main(
            pathlib.Path(args.request), pathlib.Path(args.outcome)
        )
        return
    else:
        _repair_child_main(pathlib.Path(args.request))
        return
    print(f"verified RQ3 {value['rq3_id']}")


if __name__ == "__main__":
    main()
