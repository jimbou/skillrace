"""Condition-blind skill revision from a normalized RQ3 feedback envelope."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import shutil
import tempfile
from collections.abc import Callable, Mapping
from typing import Any

from .closeai import (
    chat,
    chat_request_identity,
    is_nonproduction_chat_fixture,
    validate_call_terminal_receipt,
    validate_chat_result,
    validate_terminal_receipt,
)
from .feedback import build_feedback_envelope, validate_feedback_envelope
from .io_utils import (
    atomic_write_json,
    atomic_write_text,
    canonical_json_bytes,
    canonical_json_hash,
    file_hash,
)


REVISE_SYS = (
    "You revise a coding-agent SKILL.md using bounded evidence from testing the "
    "current skill. Correct the general procedural guidance that caused confirmed "
    "failures, clarify relevant contingencies, and add proportionate guardrails. "
    "Treat inconclusive findings as uncertain and do not claim they are defects. "
    "Preserve the skill's purpose and concise format. Generalize from the evidence; "
    "do not enumerate or memorize test cases. Output only the complete revised "
    "SKILL.md content."
)
REVISION_PROMPT_VERSION = "skillrace-rq3-revision/1"
FROZEN_REVISION_CONFIG = {
    "model": "qwen3.6-flash",
    "temperature": 0.0,
    "reasoning": True,
    "max_tokens": 4000,
    "prompt_version": REVISION_PROMPT_VERSION,
}
REVISION_USER_TEMPLATE = (
    "CURRENT SKILL.md:\n---\n{base_skill}\n---\n\n"
    "METHOD-NEUTRAL TESTING FEEDBACK (canonical JSON):\n"
    "<feedback-envelope>\n{envelope_json}\n</feedback-envelope>\n\n"
    "Output only the complete revised SKILL.md."
)
MAX_PACKAGE_FILES = 128
MAX_PACKAGE_BYTES = 4 * 1024 * 1024
CAMPAIGN_ONLY_FILES = {
    "properties.json",
    "applicability.json",
    "Containerfile.base",
    "candidate.json",
}


class RevisionError(ValueError):
    """Raised when a base or revised skill package is unsafe or malformed."""


def _package_files(root: pathlib.Path, *, include_provenance: bool = False):
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RevisionError(f"skill package symlink is forbidden: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if not include_provenance and relative.parts[:1] == (".skillrace",):
            continue
        yield relative, path


def validate_skill_package(root: str | pathlib.Path) -> pathlib.Path:
    """Validate a bounded, regular-file-only skill package."""

    raw = pathlib.Path(root)
    if raw.is_symlink():
        raise RevisionError(f"skill package symlink is forbidden: {raw}")
    resolved = raw.resolve()
    if not resolved.is_dir():
        raise RevisionError(f"skill package is not a directory: {resolved}")
    skill_md = resolved / "SKILL.md"
    if skill_md.is_symlink() or not skill_md.is_file():
        raise RevisionError(f"skill package must contain a regular SKILL.md: {resolved}")
    files = list(_package_files(resolved, include_provenance=True))
    if len(files) > MAX_PACKAGE_FILES:
        raise RevisionError(f"skill package exceeds {MAX_PACKAGE_FILES} files")
    total = sum(path.stat().st_size for _, path in files)
    if total > MAX_PACKAGE_BYTES:
        raise RevisionError(f"skill package exceeds {MAX_PACKAGE_BYTES} bytes")
    try:
        content = skill_md.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise RevisionError("SKILL.md must be UTF-8") from error
    if not content.strip():
        raise RevisionError("SKILL.md must not be empty")
    return resolved


def package_hash(root: str | pathlib.Path) -> str:
    """Hash package-relative paths and bytes, excluding revision provenance."""

    validated = validate_skill_package(root)
    digest = hashlib.sha256()
    for relative, path in _package_files(validated):
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_json_object(path: pathlib.Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise RevisionError(f"{label} symlink is forbidden: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RevisionError(f"cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise RevisionError(f"{label} must be a JSON object: {path}")
    return value


def validate_revision_artifact(
    artifact_dir: str | pathlib.Path,
    *,
    expected_base_skill_hash: str,
    expected_envelope_hash: str,
) -> dict[str, Any]:
    """Strictly validate a revision and its copied durable model-call receipt."""

    artifact = pathlib.Path(artifact_dir)
    if artifact.is_symlink() or not artifact.is_dir():
        raise RevisionError(f"revision artifact is not a regular directory: {artifact}")
    provenance = artifact / "provenance"
    record = _read_json_object(provenance / "revision.json", "revision record")
    if record.get("schema") != "skillrace-revision/2":
        raise RevisionError("revision record schema mismatch")
    if record.get("model_config") != FROZEN_REVISION_CONFIG:
        raise RevisionError("revision model configuration mismatch")
    if (
        record.get("prompt_version") != REVISION_PROMPT_VERSION
        or record.get("system_prompt") != REVISE_SYS
        or record.get("system_prompt_hash")
        != hashlib.sha256(REVISE_SYS.encode("utf-8")).hexdigest()
    ):
        raise RevisionError("revision frozen system prompt mismatch")
    user_prompt = record.get("user_prompt")
    if (
        not isinstance(user_prompt, str)
        or record.get("user_prompt_hash")
        != hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()
    ):
        raise RevisionError("revision user prompt hash mismatch")
    if record.get("base_skill_hash") != expected_base_skill_hash:
        raise RevisionError("revision base skill hash mismatch")
    if record.get("envelope_hash") != expected_envelope_hash:
        raise RevisionError("revision envelope hash mismatch")
    if (
        record.get("skill_package") != "skill"
        or record.get("raw_response") != "provenance/raw-response.txt"
        or record.get("journal_terminal_receipt")
        != "provenance/model-call-terminal.json"
        or record.get("journal_call_terminal_receipt")
        != "provenance/model-call-operation-terminal.json"
    ):
        raise RevisionError("revision package/provenance boundary mismatch")
    operation_start_identity = {
        "schema": "skillrace-revision-start/1",
        "producer": artifact.name,
        "base_skill_hash": record.get("base_skill_hash"),
        "base_package_hash": record.get("base_package_hash"),
        "envelope_hash": record.get("envelope_hash"),
        "request_hash": record.get("request_hash"),
        "model_config": record.get("model_config"),
    }
    if record.get("operation_start_identity") != operation_start_identity:
        raise RevisionError("revision operation start identity mismatch")
    operation_id = f"rq3.revision.{canonical_json_hash(operation_start_identity)}"
    if record.get("operation_id") != operation_id:
        raise RevisionError("revision operation identity mismatch")
    if record.get("provider_model") != FROZEN_REVISION_CONFIG["model"]:
        raise RevisionError("revision provider model mismatch")
    if record.get("billing_status") != "known":
        raise RevisionError("revision billing status must be known")
    usage = {}
    for source_field, target_field in (
        ("input_tokens", "prompt_tokens"),
        ("output_tokens", "completion_tokens"),
    ):
        amount = record.get(source_field)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise RevisionError(f"revision {source_field} is invalid")
        usage[target_field] = amount
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    cost = record.get("cost_usd")
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(float(cost))
        or cost < 0
    ):
        raise RevisionError("revision cost_usd is invalid")
    package = validate_skill_package(artifact / "skill")
    raw_response = provenance / "raw-response.txt"
    if raw_response.is_symlink() or not raw_response.is_file():
        raise RevisionError("revision raw response is missing or symlinked")
    if file_hash(package / "SKILL.md") != record.get("revised_skill_hash"):
        raise RevisionError("revised skill hash mismatch")
    if package_hash(package) != record.get("revised_package_hash"):
        raise RevisionError("revised package hash mismatch")
    if file_hash(raw_response) != record.get("raw_response_hash"):
        raise RevisionError("raw revision response hash mismatch")
    terminal_path = provenance / "model-call-terminal.json"
    terminal = _read_json_object(terminal_path, "model-call terminal receipt")
    if file_hash(terminal_path) != record.get("journal_terminal_receipt_hash"):
        raise RevisionError("revision journal terminal receipt hash mismatch")
    call_terminal_path = provenance / "model-call-operation-terminal.json"
    call_terminal = _read_json_object(
        call_terminal_path, "model-call operation terminal receipt"
    )
    if file_hash(call_terminal_path) != record.get(
        "journal_call_terminal_receipt_hash"
    ):
        raise RevisionError("revision journal call terminal receipt hash mismatch")
    expected_request_identity = chat_request_identity(
        [
            {"role": "system", "content": REVISE_SYS},
            {"role": "user", "content": user_prompt},
        ],
        model=FROZEN_REVISION_CONFIG["model"],
        temperature=FROZEN_REVISION_CONFIG["temperature"],
        max_tokens=FROZEN_REVISION_CONFIG["max_tokens"],
        reasoning=FROZEN_REVISION_CONFIG["reasoning"],
    )
    journal_skill = record.get("journal_skill")
    if not isinstance(journal_skill, str) or not journal_skill:
        raise RevisionError("revision journal skill identity is missing")
    if record.get("journal_tag") != "rq3.revise":
        raise RevisionError("revision journal tag identity mismatch")
    try:
        validated_terminal = validate_terminal_receipt(
            terminal,
            expected_model=FROZEN_REVISION_CONFIG["model"],
            expected_operation_id=operation_id,
            expected_usage=usage,
            expected_cost_usd=cost,
            expected_request_identity=expected_request_identity,
            expected_tag="rq3.revise",
            expected_skill=journal_skill,
        )
    except ValueError as error:
        raise RevisionError(f"revision journal terminal receipt is invalid: {error}") from error
    try:
        validated_call_terminal = validate_call_terminal_receipt(
            call_terminal,
            expected_model=FROZEN_REVISION_CONFIG["model"],
            expected_operation_id=operation_id,
            expected_last_retry_ordinal=terminal["retry_ordinal"],
            expected_request_identity=expected_request_identity,
            expected_tag="rq3.revise",
            expected_skill=journal_skill,
        )
    except ValueError as error:
        raise RevisionError(
            f"revision journal call terminal receipt is invalid: {error}"
        ) from error
    if (
        record.get("journal_terminal_event_id") != validated_terminal["event_id"]
        or record.get("provider_response_id_sha256")
        != validated_terminal["provider_response_id_sha256"]
        or record.get("provider_request_id_sha256")
        != validated_terminal["provider_request_id_sha256"]
        or record.get("journal_call_terminal_event_id")
        != validated_call_terminal["event_id"]
    ):
        raise RevisionError("revision model-call provenance mismatch")
    return record


def revision_request(base_skill: str, envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Build the one frozen request used for every feedback condition."""

    validate_feedback_envelope(envelope)
    if not isinstance(base_skill, str) or not base_skill.strip():
        raise RevisionError("base skill text must be non-empty")
    envelope_json = canonical_json_bytes(envelope).decode("utf-8")
    return {
        "system": REVISE_SYS,
        "template": REVISION_USER_TEMPLATE,
        "envelope_json": envelope_json,
        "user": REVISION_USER_TEMPLATE.format(
            base_skill=base_skill, envelope_json=envelope_json
        ),
        "model_config": dict(FROZEN_REVISION_CONFIG),
    }


def _strip_markdown_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if not lines or not lines[0].startswith("```"):
            raise RevisionError("malformed fenced model response")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text:
        raise RevisionError("model returned an empty revised skill")
    if "\x00" in text:
        raise RevisionError("model returned a NUL byte in SKILL.md")
    return text + "\n"


def _copy_base_package(source: pathlib.Path, destination: pathlib.Path) -> None:
    ignore_names = {"repo", "seeds", ".skillrace"}
    destination.mkdir()
    for relative, path in _package_files(source):
        if (
            relative.parts[0] in ignore_names
            or relative.name in CAMPAIGN_ONLY_FILES
            or relative.name.endswith(".log")
        ):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def revise_skill_package(
    skill_dir: str | pathlib.Path,
    envelope: Mapping[str, Any],
    output_dir: str | pathlib.Path,
    *,
    chat_fn: Callable[..., Mapping[str, Any]] = chat,
) -> dict[str, Any]:
    """Make exactly one frozen-model revision and write complete provenance."""

    source = validate_skill_package(skill_dir)
    validate_feedback_envelope(envelope)
    output = pathlib.Path(output_dir)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    if chat_fn is not chat and not is_nonproduction_chat_fixture(chat_fn):
        raise RevisionError(
            "custom chat_fn requires the explicit nonproduction_chat_fixture boundary"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    current = (source / "SKILL.md").read_text(encoding="utf-8")
    request = revision_request(current, envelope)
    config = request["model_config"]
    durable_start_identity = {
        "schema": "skillrace-revision-start/1",
        "producer": output.name,
        "base_skill_hash": file_hash(source / "SKILL.md"),
        "base_package_hash": package_hash(source),
        "envelope_hash": canonical_json_hash(envelope),
        "request_hash": canonical_json_hash(request),
        "model_config": dict(config),
    }
    operation_id = f"rq3.revision.{canonical_json_hash(durable_start_identity)}"
    messages = [
        {"role": "system", "content": request["system"]},
        {"role": "user", "content": request["user"]},
    ]
    expected_request_identity = chat_request_identity(
        messages,
        model=config["model"],
        temperature=config["temperature"],
        max_tokens=config["max_tokens"],
        reasoning=config["reasoning"],
    )
    response = chat_fn(
        messages,
        model=config["model"],
        temperature=config["temperature"],
        reasoning=config["reasoning"],
        max_tokens=config["max_tokens"],
        tag="rq3.revise",
        skill=source.name,
        operation_id=operation_id,
    )
    try:
        validate_chat_result(
            response,
            expected_model=config["model"],
            expected_operation_id=operation_id,
            expected_request_identity=expected_request_identity,
            expected_tag="rq3.revise",
            expected_skill=source.name,
        )
    except ValueError as error:
        raise RevisionError(f"revision model response provenance is invalid: {error}") from error
    raw_response = response["content"]
    revised = _strip_markdown_fence(raw_response)
    usage = response["usage"]

    temporary = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent)
    )
    try:
        artifact = temporary / "artifact"
        artifact.mkdir()
        package = artifact / "skill"
        _copy_base_package(source, package)
        atomic_write_text(package / "SKILL.md", revised)
        validate_skill_package(package)
        provenance = artifact / "provenance"
        provenance.mkdir()
        atomic_write_text(provenance / "raw-response.txt", raw_response)
        atomic_write_text(
            provenance / "model-call-terminal.json",
            canonical_json_bytes(response["journal_terminal_receipt"]).decode("utf-8")
            + "\n",
        )
        atomic_write_text(
            provenance / "model-call-operation-terminal.json",
            canonical_json_bytes(
                response["journal_call_terminal_receipt"]
            ).decode("utf-8")
            + "\n",
        )
        record = {
            "schema": "skillrace-revision/2",
            "skill_package": "skill",
            "raw_response": "provenance/raw-response.txt",
            "prompt_version": REVISION_PROMPT_VERSION,
            "system_prompt": request["system"],
            "user_prompt": request["user"],
            "system_prompt_hash": hashlib.sha256(
                request["system"].encode("utf-8")
            ).hexdigest(),
            "user_prompt_hash": hashlib.sha256(
                request["user"].encode("utf-8")
            ).hexdigest(),
            "request_hash": canonical_json_hash(request),
            "model_config": dict(config),
            "operation_start_identity": durable_start_identity,
            "operation_id": response["operation_id"],
            "provider_model": response["provider_model"],
            "provider_response_id_sha256": response[
                "provider_response_id_sha256"
            ],
            "provider_request_id_sha256": response[
                "provider_request_id_sha256"
            ],
            "billing_status": response["billing_status"],
            "journal_tag": "rq3.revise",
            "journal_skill": source.name,
            "journal_terminal_event_id": response["journal_terminal_event_id"],
            "journal_terminal_receipt": "provenance/model-call-terminal.json",
            "journal_terminal_receipt_hash": file_hash(
                provenance / "model-call-terminal.json"
            ),
            "journal_call_terminal_event_id": response[
                "journal_call_terminal_event_id"
            ],
            "journal_call_terminal_receipt": (
                "provenance/model-call-operation-terminal.json"
            ),
            "journal_call_terminal_receipt_hash": file_hash(
                provenance / "model-call-operation-terminal.json"
            ),
            "base_skill_hash": file_hash(source / "SKILL.md"),
            "base_package_hash": package_hash(source),
            "envelope_hash": canonical_json_hash(envelope),
            "raw_response_hash": hashlib.sha256(raw_response.encode("utf-8")).hexdigest(),
            "revised_skill_hash": file_hash(package / "SKILL.md"),
            "revised_package_hash": package_hash(package),
            "input_tokens": usage["prompt_tokens"],
            "output_tokens": usage["completion_tokens"],
            "cost_usd": response["cost_usd"],
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        atomic_write_json(provenance / "revision.json", record)
        artifact.rename(output)
        temporary.rmdir()
        return record
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def build_feedback_payload(campaign_path: str | pathlib.Path, max_bytes: int = 24000) -> str:
    """Compatibility helper: project a campaign and return canonical envelope JSON."""

    campaign = json.loads(pathlib.Path(campaign_path).read_text(encoding="utf-8"))
    envelope = build_feedback_envelope(campaign, max_bytes=max_bytes)
    return canonical_json_bytes(envelope).decode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Revise a SKILL.md from a method-neutral RQ3 feedback envelope"
    )
    parser.add_argument("--skill-dir", required=True)
    parser.add_argument("--envelope", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    envelope = json.loads(pathlib.Path(args.envelope).read_text(encoding="utf-8"))
    record = revise_skill_package(args.skill_dir, envelope, args.out)
    print(
        f"revised SKILL.md -> {pathlib.Path(args.out) / record['skill_package'] / 'SKILL.md'} "
        f"(${record['cost_usd']:.4f})"
    )


if __name__ == "__main__":
    main()
