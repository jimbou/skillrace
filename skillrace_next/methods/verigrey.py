import copy
import json
from pathlib import Path
from typing import Any, Callable
import uuid

from ..pipeline.stages import validate_generated_dockerfile, validate_test
from ..records import ExperimentConfig, SkillVersion, TestCase
from ..runtime.pi import PiRequest, PiResult, run_pi
from ..storage import atomic_write_json, file_hash, tree_hash


PiRunner = Callable[[PiRequest], PiResult]
TestValidator = Callable[[TestCase, ExperimentConfig], TestCase]
_STATE_FIELDS = {
    "schema",
    "tool_counts",
    "transition_counts",
    "sequence_counts",
    "last_observation",
}


def _argument_shape(value: Any) -> Any:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return {
            str(key): _argument_shape(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        shapes = {_canonical(_argument_shape(item)) for item in value}
        return [json.loads(item) for item in sorted(shapes)] if shapes else []
    return type(value).__name__


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def normalize_tool_sequence(trace: str | Path) -> list[dict[str, Any]]:
    sequence: list[dict[str, Any]] = []
    for line in Path(trace).read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message", {})
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "toolCall":
                continue
            name = item.get("name")
            arguments = item.get("arguments", {})
            if not isinstance(name, str) or not name or not isinstance(arguments, dict):
                continue
            sequence.append(
                {"tool": name, "arguments": _argument_shape(arguments)}
            )
    return sequence


def _validate_tool(tool: Any) -> dict[str, Any]:
    if (
        not isinstance(tool, dict)
        or set(tool) != {"tool", "arguments"}
        or not isinstance(tool["tool"], str)
        or not tool["tool"]
        or not isinstance(tool["arguments"], dict)
    ):
        raise ValueError("normalized tools must contain a name and argument shape")
    json.dumps(tool, sort_keys=True)
    return copy.deepcopy(tool)


def _increment(
    records: list[dict[str, Any]],
    identity: dict[str, Any],
) -> tuple[int, bool]:
    key_names = tuple(identity)
    for record in records:
        if all(record.get(name) == identity[name] for name in key_names):
            record["count"] += 1
            return record["count"], False
    records.append({**copy.deepcopy(identity), "count": 1})
    return 1, True


def _new_state() -> dict[str, Any]:
    return {
        "schema": "skillrace-verigrey-state/1",
        "tool_counts": [],
        "transition_counts": [],
        "sequence_counts": [],
        "last_observation": None,
    }


def _validate_state(state: Any) -> dict[str, Any]:
    if state == {}:
        return _new_state()
    copied = copy.deepcopy(state)
    if (
        not isinstance(copied, dict)
        or set(copied) != _STATE_FIELDS
        or copied.get("schema") != "skillrace-verigrey-state/1"
        or not all(
            isinstance(copied[name], list)
            for name in ("tool_counts", "transition_counts", "sequence_counts")
        )
    ):
        raise ValueError("VeriGrey state is invalid")
    return copied


def update_state(
    state: dict[str, Any],
    sequence: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(sequence, list) or not sequence:
        raise ValueError("normalized tool sequence must be nonempty")
    normalized = [_validate_tool(tool) for tool in sequence]
    updated = _validate_state(state)
    tool_counts: list[int] = []
    novel_tools: list[dict[str, Any]] = []
    for tool in normalized:
        count, novel = _increment(updated["tool_counts"], {"tool": tool})
        tool_counts.append(count)
        if novel and tool not in novel_tools:
            novel_tools.append(copy.deepcopy(tool))
    transition_counts: list[int] = []
    novel_transitions: list[dict[str, Any]] = []
    for source, target in zip(normalized, normalized[1:], strict=False):
        transition = {"source": source, "target": target}
        count, novel = _increment(updated["transition_counts"], transition)
        transition_counts.append(count)
        if novel:
            novel_transitions.append(copy.deepcopy(transition))
    sequence_count, novel_sequence = _increment(
        updated["sequence_counts"], {"sequence": normalized}
    )
    updated["last_observation"] = {
        "sequence": copy.deepcopy(normalized),
        "novelty_delta": {
            "tools": novel_tools,
            "transitions": novel_transitions,
            "sequence": novel_sequence,
        },
        "coverage_counts": {
            "tools": tool_counts,
            "transitions": transition_counts,
            "sequence": sequence_count,
        },
    }
    return updated


def _novelty_target(state: dict[str, Any]) -> dict[str, Any]:
    validated = _validate_state(state)
    transitions = validated["transition_counts"]
    if not transitions:
        raise ValueError("VeriGrey state has no tool transition to target")
    return copy.deepcopy(
        min(transitions, key=lambda item: (item["count"], _canonical(item)))
    )


def _assistant_json(trace_path: Path) -> dict[str, str]:
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
        raise ValueError("VeriGrey proposal contains no assistant JSON")
    parsed = json.loads(responses[-1])
    if not isinstance(parsed, dict) or set(parsed) != {"prompt", "dockerfile"}:
        raise ValueError("VeriGrey proposal response is invalid")
    if not all(isinstance(parsed[name], str) and parsed[name].strip() for name in parsed):
        raise ValueError("VeriGrey proposal fields must be nonempty")
    return {name: parsed[name].strip() for name in parsed}


def propose_test(
    state: dict[str, Any],
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    config: ExperimentConfig,
    *,
    pi_runner: PiRunner = run_pi,
    validator: TestValidator = validate_test,
) -> TestCase:
    property_ids = [item.get("property_id") for item in properties]
    if (
        not properties
        or not all(isinstance(item, str) and item for item in property_ids)
        or len(set(property_ids)) != len(properties)
    ):
        raise ValueError("properties must contain unique property IDs")
    target = _novelty_target(state)
    validated_state = _validate_state(state)
    observation = validated_state["last_observation"]
    if not isinstance(observation, dict):
        raise ValueError("VeriGrey state has no tool-sequence evidence")
    output = config.output_root / "verigrey-proposals" / uuid.uuid4().hex
    response: dict[str, str] | None = None
    result: PiResult | None = None
    diagnostic: str | None = None
    for ordinal in (1, 2):
        attempt = output / f"proposal-attempt-{ordinal}"
        attempt.mkdir(parents=True)
        correction = (
            f" Your previous response was invalid: {diagnostic}. Return corrected raw "
            "JSON only."
            if diagnostic
            else ""
        )
        prompt_path = attempt / "prompt.txt"
        prompt_path.write_text(
            "Propose one concrete development task that exercises the supplied least-covered "
            "tool transition and must meaningfully exercise the supplied skill. A generic "
            "task that merely reaches the transition is invalid; the transition is not a "
            "substitute for skill relevance. Make all inline data, expected values, examples, "
            "and prose internally consistent; do not emit mutually inconsistent requirements. "
            "The task must be self-contained. Generate its visible prompt and complete "
            "Dockerfile. The Dockerfile may create task inputs, and the prompt must accurately "
            "describe them. Put task and artifact paths under /workspace and do not use "
            "/mnt/data or /tmp in the task prompt. The Dockerfile must be no larger than "
            f"32 KiB, start with exactly 'FROM {config.docker_image}', contain exactly one "
            "FROM, use no ADD or COPY, and contain exactly 'WORKDIR /workspace'. Preserve "
            "the installed Pi runtime. The task must be compatible with every fixed property, "
            "and all property requirements must be consistent with the visible prompt. Return "
            "only one JSON object with exactly prompt and dockerfile; both values must be "
            "nonempty strings. Do not return check prose, check IDs, or any other keys. The "
            "entire response must start with { and end with }. Do not use Markdown fences or "
            f"tools.{correction}\n\n"
            f"NOVELTY TARGET:\n{json.dumps(target, sort_keys=True)}\n\n"
            f"RECENT TOOL-SEQUENCE EVIDENCE:\n{json.dumps(observation, sort_keys=True)}\n\n"
            f"FIXED PROPERTIES:\n{json.dumps(properties, sort_keys=True)}\n\n"
            f"SKILL.md:\n{(skill.directory_path / 'SKILL.md').read_text(encoding='utf-8')}\n",
            encoding="utf-8",
        )
        result = pi_runner(
            PiRequest(
                operation_id=f"proposal.verigrey.{uuid.uuid4().hex}",
                provider=config.provider,
                model=config.model_id,
                prompt_path=prompt_path,
                output_dir=attempt,
                image=config.docker_image,
                allowed_tools=("read",),
                max_turns=config.role_budgets["proposer"],
                timeout_seconds=config.timeouts["pi"],
            )
        )
        if result.status != "completed":
            raise RuntimeError(f"Pi VeriGrey proposal failed: {result.status}")
        try:
            response = _assistant_json(result.trace_path)
            response["dockerfile"] = validate_generated_dockerfile(
                response["dockerfile"], config.docker_image
            )
            break
        except (json.JSONDecodeError, OSError, ValueError) as error:
            diagnostic = str(error)
    if response is None or result is None:
        raise ValueError("two malformed VeriGrey proposal responses")
    test_id = "verigrey-" + uuid.uuid4().hex
    case = output / test_id
    environment = case / "environment"
    environment.mkdir(parents=True)
    task_path = case / "prompt.txt"
    task_path.write_text(response["prompt"] + "\n", encoding="utf-8")
    checks_path = case / "nl_checks.json"
    atomic_write_json(checks_path, properties)
    (environment / "Dockerfile").write_text(
        response["dockerfile"], encoding="utf-8"
    )
    atomic_write_json(environment / "sanity.json", {"status": "pass"})
    prompt_hash = file_hash(task_path)
    environment_hash = tree_hash(environment)
    catalog_hash = file_hash(checks_path)
    proposal_receipt = case / "proposal.json"
    atomic_write_json(
        proposal_receipt,
        {
            "schema": "skillrace-generated-test-proposal/1",
            "method": "verigrey",
            "catalog_hash": catalog_hash,
            "prompt_hash": prompt_hash,
            "environment_hash": environment_hash,
            "novelty_target": target,
            "tool_sequence_evidence": observation,
            "pi_receipt_path": str(result.receipt_path),
            "pi_receipt_hash": file_hash(result.receipt_path),
            "model": config.model_id,
        },
    )
    proposed = TestCase(
        test_id=test_id,
        prompt_path=task_path,
        prompt_hash=prompt_hash,
        environment_directory=environment,
        environment_hash=environment_hash,
        nl_check_path=checks_path,
        nl_check_hash=catalog_hash,
        origin_method="verigrey",
        proposal_receipt=proposal_receipt,
        validation_status="pending",
        validation_diagnostic="",
        container_image_id="",
    )
    return validator(proposed, config)
