"""Single-call, execution-blind backend for patching one ``SKILL.md``."""

from __future__ import annotations

import pathlib
from collections.abc import Mapping
from typing import Any

from .closeai import (
    chat,
    chat_request_identity,
    is_nonproduction_chat_fixture,
    validate_chat_result,
)
from .io_utils import atomic_write_text, canonical_json_bytes, canonical_json_hash
from .repair_validation import FailureRepairRequest
from .revise_skill import (
    copy_base_skill_package,
    normalize_revised_skill,
    package_hash,
    validate_skill_package,
)


DIRECT_PATCH_SYSTEM_PROMPT = (
    "You make one blind repair to a coding-agent SKILL.md. Output only the complete "
    "replacement SKILL.md. Prefer the smallest additive clarification or guardrail "
    "that addresses the failure. Preserve useful existing guidance and do not rewrite "
    "unrelated sections; remove or replace text only when it directly caused the "
    "failure. Add actionable procedural guidance that could change a future agent's "
    "behavior. A cosmetic-only edit, docstring rewording, or unsupported claim that "
    "the current behavior already handles the failure is not a repair. Generalize the "
    "procedural fix; do not memorize concrete "
    "test values or checker wording. You must not rerun or execute the failure, run "
    "tests, invoke a checker, replay the agent, validate the patch, repair the failed "
    "artifact, or iterate patch-and-test. Do not provide a rationale or claim that "
    "the patch works."
)


def make_direct_patcher(
    *,
    model: str,
    timeout_seconds: int = 300,
    max_tokens: int = 4000,
    temperature: float = 0.0,
    reasoning: bool = True,
    chat_fn=chat,
):
    """Return a backend that performs exactly one semantic model patch call."""

    if not isinstance(model, str) or not model or len(model) > 128:
        raise ValueError("direct patch model must be bounded text")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 600:
        raise ValueError("direct patch timeout must be in 1..600")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 65536:
        raise ValueError("direct patch output limit must be in 1..65536")
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= float(temperature) <= 2:
        raise ValueError("direct patch temperature must be in 0..2")
    if not isinstance(reasoning, bool):
        raise ValueError("direct patch reasoning must be boolean")
    if chat_fn is not chat and not is_nonproduction_chat_fixture(chat_fn):
        raise ValueError("custom direct patch chat requires a nonproduction fixture")

    config: dict[str, Any] = {
        "backend": "direct",
        "model": model,
        "timeout_seconds": timeout_seconds,
        "max_output_tokens": max_tokens,
        "temperature": float(temperature),
        "reasoning": reasoning,
        "prompt_version": "skillrace-direct-patch/3",
    }

    def patcher(
        request: FailureRepairRequest,
        evidence: Mapping[str, Any],
        work_dir: pathlib.Path,
    ) -> dict[str, Any]:
        source = validate_skill_package(request.original_skill_dir)
        if package_hash(source) != request.original_skill_hash:
            raise ValueError("direct patch source differs from request")
        payload = evidence.get("reviser_payload")
        if not isinstance(payload, Mapping) or evidence.get("evidence_hash") != canonical_json_hash(payload):
            raise ValueError("direct patch evidence identity mismatch")
        common = {
            "schema": payload.get("schema"),
            "original_skill_hash": payload.get("original_skill_hash"),
            "failure_core": payload.get("failure_core"),
        }
        current = (source / "SKILL.md").read_text(encoding="utf-8")
        prompt = (
            "CURRENT SKILL.md:\n<current-skill>\n"
            + current
            + "</current-skill>\n\nCOMMON FAILED-EXECUTION EVIDENCE (canonical JSON):\n"
            + canonical_json_bytes(common).decode("utf-8")
            + "\n\nOutput only the complete replacement SKILL.md and stop."
        )
        messages = [
            {"role": "system", "content": DIRECT_PATCH_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        operation_id = "repair.direct." + canonical_json_hash(
            {
                "request": request.identity(),
                "evidence_hash": evidence["evidence_hash"],
                "config": config,
                "messages": messages,
            }
        )
        expected = chat_request_identity(
            messages,
            model=model,
            temperature=float(temperature),
            max_tokens=max_tokens,
            reasoning=reasoning,
        )
        response = chat_fn(
            messages,
            model=model,
            temperature=float(temperature),
            reasoning=reasoning,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            retries=1,
            tag="repair.direct-patch",
            skill=request.skill_name,
            operation_id=operation_id,
        )
        validate_chat_result(
            response,
            expected_model=model,
            expected_operation_id=operation_id,
            expected_request_identity=expected,
            expected_tag="repair.direct-patch",
            expected_skill=request.skill_name,
        )
        revised = normalize_revised_skill(response["content"])
        work = pathlib.Path(work_dir).resolve()
        work.mkdir(parents=True, exist_ok=True)
        skill = copy_base_skill_package(source, work / "skill")
        atomic_write_text(skill / "SKILL.md", revised)
        validate_skill_package(skill)
        return {
            "status": "completed",
            "skill_dir": str(skill),
            "backend": "direct",
            "model": model,
            "operation_id": response["operation_id"],
            "input_tokens": int(response["usage"]["prompt_tokens"]),
            "output_tokens": int(response["usage"]["completion_tokens"]),
            "cost_provider_credits": float(response.get("cost_provider_credits") or 0.0),
            "timeout_seconds": timeout_seconds,
        }

    patcher.backend_name = "direct"
    patcher.model = model
    patcher.timeout_seconds = timeout_seconds
    patcher.config = config
    return patcher
