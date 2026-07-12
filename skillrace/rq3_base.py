"""Exactly-once zero-shot base-skill generation with complete public provenance."""

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
from .io_utils import (
    atomic_write_json,
    atomic_write_text,
    canonical_json_bytes,
    canonical_json_hash,
    file_hash,
)
from .revise_skill import package_hash, validate_skill_package


BASE_GENERATION_SYSTEM = (
    "Create a concise, general coding-agent skill for the public purpose. Include "
    "contingencies, validation steps, and practical guardrails without inventing or "
    "memorizing evaluation cases. Output only the complete SKILL.md."
)
BASE_GENERATION_PROMPT_VERSION = "skillrace-rq3-base-generation/1"
FROZEN_BASE_GENERATION_CONFIG = {
    "model": "qwen3.6-flash",
    "temperature": 0.0,
    "reasoning": True,
    "max_tokens": 4000,
    "prompt_version": BASE_GENERATION_PROMPT_VERSION,
}


def _read(path: pathlib.Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"{label} symlink is forbidden: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _strip_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        text = "\n".join(lines).strip()
    if not text or "\x00" in text:
        raise ValueError("base-generation response is empty or contains NUL")
    return text + "\n"


def _prompt(scenario_id: str, purpose: str) -> str:
    return (
        f"SCENARIO ID: {scenario_id}\n\n"
        "PUBLIC PURPOSE:\n---\n"
        f"{purpose.rstrip()}\n---\n\n"
        "Output only the complete SKILL.md."
    )


def validate_base_generation(base_skill_dir: str | pathlib.Path) -> dict[str, Any]:
    root = validate_skill_package(base_skill_dir)
    provenance = root / ".skillrace"
    record_path = provenance / "base-generation.json"
    receipt_path = provenance / "receipt.json"
    prompt_path = provenance / "prompt.txt"
    response_path = provenance / "raw-response.txt"
    start_path = provenance / "start.json"
    model_call_path = provenance / "model-call-terminal.json"
    call_terminal_path = provenance / "model-call-operation-terminal.json"
    record = _read(record_path, "base-generation record")
    receipt = _read(receipt_path, "base-generation receipt")
    terminal = _read(model_call_path, "model-call terminal receipt")
    call_terminal = _read(call_terminal_path, "model-call operation terminal receipt")
    if record.get("schema") != "skillrace-base-generation/2":
        raise ValueError("unsupported base-generation record")
    if record.get("model_config") != FROZEN_BASE_GENERATION_CONFIG:
        raise ValueError("base-generation model configuration mismatch")
    if (
        record.get("prompt_version") != BASE_GENERATION_PROMPT_VERSION
        or record.get("system_prompt") != BASE_GENERATION_SYSTEM
        or record.get("system_prompt_hash")
        != hashlib.sha256(BASE_GENERATION_SYSTEM.encode("utf-8")).hexdigest()
    ):
        raise ValueError("base-generation frozen system prompt mismatch")
    operation_id = record.get("operation_id")
    if not isinstance(operation_id, str):
        raise ValueError("base-generation operation identity is missing")
    start = _read(start_path, "base-generation start")
    if start.get("schema") != "skillrace-base-generation-start/2":
        raise ValueError("base-generation start schema mismatch")
    start_identity = {
        key: value for key, value in start.items() if key != "operation_id"
    }
    if (
        start.get("operation_id") != operation_id
        or operation_id != f"rq3.base.{canonical_json_hash(start_identity)}"
    ):
        raise ValueError("base-generation operation identity mismatch")
    if record.get("provider_model") != FROZEN_BASE_GENERATION_CONFIG["model"]:
        raise ValueError("base-generation provider model mismatch")
    if record.get("journal_terminal_receipt") != (
        ".skillrace/model-call-terminal.json"
    ):
        raise ValueError("base-generation journal terminal receipt path mismatch")
    if record.get("journal_call_terminal_receipt") != (
        ".skillrace/model-call-operation-terminal.json"
    ):
        raise ValueError("base-generation journal call terminal receipt path mismatch")
    if file_hash(root / "SKILL.md") != record.get("skill_hash"):
        raise ValueError("base-generation skill hash mismatch")
    if package_hash(root) != record.get("package_hash"):
        raise ValueError("base-generation package hash mismatch")
    if file_hash(prompt_path) != record.get("prompt_hash"):
        raise ValueError("base-generation prompt hash mismatch")
    if file_hash(response_path) != record.get("raw_response_hash"):
        raise ValueError("base-generation raw response hash mismatch")
    if file_hash(start_path) != record.get("start_hash"):
        raise ValueError("base-generation start hash mismatch")
    if file_hash(model_call_path) != record.get("journal_terminal_receipt_hash"):
        raise ValueError("base-generation journal terminal receipt hash mismatch")
    if file_hash(call_terminal_path) != record.get(
        "journal_call_terminal_receipt_hash"
    ):
        raise ValueError("base-generation journal call terminal receipt hash mismatch")
    if (
        receipt.get("schema") != "skillrace-base-generation-receipt/2"
        or receipt.get("generation_id") != record.get("generation_id")
        or receipt.get("start_hash") != record.get("start_hash")
        or receipt.get("record_hash") != canonical_json_hash(record)
        or receipt.get("skill_hash") != record.get("skill_hash")
        or receipt.get("raw_response_hash") != record.get("raw_response_hash")
    ):
        raise ValueError("base-generation receipt mismatch")
    for field in ("input_tokens", "output_tokens"):
        if not isinstance(record.get(field), int) or record[field] < 0:
            raise ValueError(f"base-generation {field} is invalid")
    cost = record.get("cost_usd")
    if (
        not isinstance(cost, (int, float))
        or isinstance(cost, bool)
        or not math.isfinite(float(cost))
        or cost < 0
    ):
        raise ValueError("base-generation cost is invalid")
    usage = {
        "prompt_tokens": record["input_tokens"],
        "completion_tokens": record["output_tokens"],
        "total_tokens": record["input_tokens"] + record["output_tokens"],
    }
    expected_request_identity = chat_request_identity(
        [
            {"role": "system", "content": BASE_GENERATION_SYSTEM},
            {"role": "user", "content": prompt_path.read_text(encoding="utf-8")},
        ],
        model=FROZEN_BASE_GENERATION_CONFIG["model"],
        temperature=FROZEN_BASE_GENERATION_CONFIG["temperature"],
        max_tokens=FROZEN_BASE_GENERATION_CONFIG["max_tokens"],
        reasoning=FROZEN_BASE_GENERATION_CONFIG["reasoning"],
    )
    validated_terminal = validate_terminal_receipt(
        terminal,
        expected_model=FROZEN_BASE_GENERATION_CONFIG["model"],
        expected_operation_id=operation_id,
        expected_usage=usage,
        expected_cost_usd=cost,
        expected_request_identity=expected_request_identity,
        expected_tag="rq3.base-generate",
        expected_skill=record.get("scenario_id"),
    )
    validated_call_terminal = validate_call_terminal_receipt(
        call_terminal,
        expected_model=FROZEN_BASE_GENERATION_CONFIG["model"],
        expected_operation_id=operation_id,
        expected_last_retry_ordinal=terminal["retry_ordinal"],
        expected_request_identity=expected_request_identity,
        expected_tag="rq3.base-generate",
        expected_skill=record.get("scenario_id"),
    )
    if (
        record.get("billing_status") != "known"
        or record.get("journal_terminal_event_id")
        != validated_terminal["event_id"]
        or record.get("provider_response_id_sha256")
        != validated_terminal["provider_response_id_sha256"]
        or record.get("provider_request_id_sha256")
        != validated_terminal["provider_request_id_sha256"]
        or record.get("journal_call_terminal_event_id")
        != validated_call_terminal["event_id"]
    ):
        raise ValueError("base-generation model-call provenance mismatch")
    return record


def generate_base_skill(
    *,
    scenario_id: str,
    purpose_path: str | pathlib.Path,
    output_dir: str | pathlib.Path,
    chat_fn: Callable[..., Mapping[str, Any]] = chat,
) -> dict[str, Any]:
    """Generate once; a start without terminal output is an honest manual-recovery stop."""

    if chat_fn is not chat and not is_nonproduction_chat_fixture(chat_fn):
        raise ValueError(
            "custom chat_fn requires the explicit nonproduction_chat_fixture boundary"
        )
    if not isinstance(scenario_id, str) or not scenario_id or "/" in scenario_id:
        raise ValueError("scenario_id must be a safe non-empty name")
    purpose_path = pathlib.Path(purpose_path)
    if purpose_path.is_symlink() or not purpose_path.is_file():
        raise ValueError("scenario purpose must be a regular file")
    purpose = purpose_path.read_text(encoding="utf-8")
    output = pathlib.Path(output_dir)
    start_path = output.with_name(f"{output.name}.start.json")
    user_prompt = _prompt(scenario_id, purpose)
    start = {
        "schema": "skillrace-base-generation-start/2",
        "scenario_id": scenario_id,
        "purpose_hash": file_hash(purpose_path),
        "system_prompt_hash": hashlib.sha256(
            BASE_GENERATION_SYSTEM.encode("utf-8")
        ).hexdigest(),
        "user_prompt_hash": hashlib.sha256(user_prompt.encode("utf-8")).hexdigest(),
        "model_config": dict(FROZEN_BASE_GENERATION_CONFIG),
    }
    start["operation_id"] = f"rq3.base.{canonical_json_hash(start)}"
    if output.exists():
        if not start_path.is_file() or _read(start_path, "base-generation start") != start:
            raise ValueError("base-generation start identity mismatch")
        return validate_base_generation(output)
    if start_path.exists():
        if _read(start_path, "base-generation start") != start:
            raise ValueError("base-generation start identity mismatch")
        from .rq3 import UncertainExternalOutcomeError

        raise UncertainExternalOutcomeError(
            "base-generation outcome is unknown; durable start exists without a terminal package"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(start_path, start)
    config = FROZEN_BASE_GENERATION_CONFIG
    messages = [
        {"role": "system", "content": BASE_GENERATION_SYSTEM},
        {"role": "user", "content": user_prompt},
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
        tag="rq3.base-generate",
        skill=scenario_id,
        operation_id=start["operation_id"],
    )
    validate_chat_result(
        response,
        expected_model=config["model"],
        expected_operation_id=start["operation_id"],
        expected_request_identity=expected_request_identity,
        expected_tag="rq3.base-generate",
        expected_skill=scenario_id,
    )
    raw = response["content"]
    skill = _strip_fence(raw)
    usage = response["usage"]
    temporary = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent)
    )
    try:
        package = temporary / "package"
        provenance = package / ".skillrace"
        provenance.mkdir(parents=True)
        atomic_write_text(package / "SKILL.md", skill)
        atomic_write_text(provenance / "prompt.txt", user_prompt)
        atomic_write_text(provenance / "raw-response.txt", raw)
        atomic_write_json(provenance / "start.json", start)
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
        generation_id = canonical_json_hash(start)[:24]
        record = {
            "schema": "skillrace-base-generation/2",
            "generation_id": generation_id,
            "scenario_id": scenario_id,
            "purpose_path": "scenario.md",
            "purpose_hash": start["purpose_hash"],
            "prompt_version": BASE_GENERATION_PROMPT_VERSION,
            "system_prompt": BASE_GENERATION_SYSTEM,
            "system_prompt_hash": start["system_prompt_hash"],
            "prompt_hash": file_hash(provenance / "prompt.txt"),
            "raw_response_hash": file_hash(provenance / "raw-response.txt"),
            "start_hash": file_hash(provenance / "start.json"),
            "skill_hash": file_hash(package / "SKILL.md"),
            "package_hash": package_hash(package),
            "model_config": dict(config),
            "operation_id": response["operation_id"],
            "provider_model": response["provider_model"],
            "provider_response_id_sha256": response[
                "provider_response_id_sha256"
            ],
            "provider_request_id_sha256": response[
                "provider_request_id_sha256"
            ],
            "billing_status": response["billing_status"],
            "journal_terminal_event_id": response["journal_terminal_event_id"],
            "journal_terminal_receipt": ".skillrace/model-call-terminal.json",
            "journal_terminal_receipt_hash": file_hash(
                provenance / "model-call-terminal.json"
            ),
            "journal_call_terminal_event_id": response[
                "journal_call_terminal_event_id"
            ],
            "journal_call_terminal_receipt": (
                ".skillrace/model-call-operation-terminal.json"
            ),
            "journal_call_terminal_receipt_hash": file_hash(
                provenance / "model-call-operation-terminal.json"
            ),
            "input_tokens": usage["prompt_tokens"],
            "output_tokens": usage["completion_tokens"],
            "cost_usd": response["cost_usd"],
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        atomic_write_json(provenance / "base-generation.json", record)
        atomic_write_json(
            provenance / "receipt.json",
            {
                "schema": "skillrace-base-generation-receipt/2",
                "generation_id": generation_id,
                "start_hash": record["start_hash"],
                "record_hash": canonical_json_hash(record),
                "skill_hash": record["skill_hash"],
                "raw_response_hash": record["raw_response_hash"],
            },
        )
        package.rename(output)
        temporary.rmdir()
        return validate_base_generation(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or verify an RQ3 zero-shot base skill with provenance"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--scenario-id", required=True)
    generate.add_argument("--purpose", required=True)
    generate.add_argument("--out", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--base-skill", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "generate":
        record = generate_base_skill(
            scenario_id=args.scenario_id,
            purpose_path=args.purpose,
            output_dir=args.out,
        )
    else:
        record = validate_base_generation(args.base_skill)
    print(f"verified base generation {record['generation_id']}")


if __name__ == "__main__":
    main()
