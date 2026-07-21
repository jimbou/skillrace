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
    if not isinstance(sequence, list):
        raise ValueError("normalized tool sequence must be a list")
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
    response = responses[-1].strip()
    if response.startswith("```json\n") and response.endswith("\n```"):
        response = response[len("```json\n") : -len("\n```")]
    parsed = json.loads(response)
    if not isinstance(parsed, dict) or set(parsed) != {"prompt", "dockerfile"}:
        raise ValueError("VeriGrey proposal response is invalid")
    if not all(isinstance(parsed[name], str) and parsed[name].strip() for name in parsed):
        raise ValueError("VeriGrey proposal fields must be nonempty")
    return {name: parsed[name].strip() for name in parsed}


def _materialize_initial_seed(
    seed_id: str,
    focus: dict[str, Any],
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    config: ExperimentConfig,
    output: Path,
    pi_runner: PiRunner,
    validator: TestValidator,
) -> TestCase:
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
            "Materialize one initial VeriGrey development seed. The supplied property is "
            "the generation focus, but the resulting task must be compatible with the "
            "complete fixed property catalog and meaningfully exercise the supplied skill. "
            "Generate the visible task prompt and its complete Dockerfile. Put every task "
            "and artifact path under /workspace; do not use /mnt/data or /tmp in the task "
            "prompt. The Dockerfile must be no larger than 32 KiB, start with exactly "
            f"'FROM {config.docker_image}', contain exactly one FROM, use no ADD or COPY, "
            "and contain exactly 'WORKDIR /workspace'. Preserve the installed Pi runtime. "
            "Return only one JSON object with exactly prompt and dockerfile, both nonempty "
            f"strings. Do not return checks, IDs, Markdown, or use tools.{correction}\n\n"
            f"SEED FOCUS:\n{json.dumps(focus, sort_keys=True)}\n\n"
            f"COMPLETE FIXED PROPERTIES:\n{json.dumps(properties, sort_keys=True)}\n\n"
            f"SKILL.md:\n{(skill.directory_path / 'SKILL.md').read_text(encoding='utf-8')}\n",
            encoding="utf-8",
        )
        result = pi_runner(
            PiRequest(
                operation_id=f"proposal.verigrey.seed.{seed_id}.{uuid.uuid4().hex}",
                provider=config.provider,
                model=config.model_id,
                prompt_path=prompt_path,
                output_dir=attempt,
                image=config.docker_image,
                allowed_tools=("read",),
                max_turns=config.role_budgets["proposer"],
                timeout_seconds=config.timeouts["pi"],
                temperature=1.0,
            )
        )
        if result.status != "completed":
            raise RuntimeError(f"Pi VeriGrey seed proposal failed: {result.status}")
        try:
            response = _assistant_json(result.trace_path)
            response["dockerfile"] = validate_generated_dockerfile(
                response["dockerfile"], config.docker_image
            )
            break
        except (json.JSONDecodeError, OSError, ValueError) as error:
            diagnostic = str(error)
    if response is None or result is None:
        raise ValueError("two malformed VeriGrey seed responses")

    case_dir = output / seed_id
    environment = case_dir / "environment"
    environment.mkdir(parents=True)
    task_path = case_dir / "prompt.txt"
    task_path.write_text(response["prompt"] + "\n", encoding="utf-8")
    checks_path = case_dir / "nl_checks.json"
    atomic_write_json(checks_path, properties)
    (environment / "Dockerfile").write_text(response["dockerfile"], encoding="utf-8")
    atomic_write_json(environment / "sanity.json", {"status": "pass"})
    prompt_hash = file_hash(task_path)
    environment_hash = tree_hash(environment)
    catalog_hash = file_hash(checks_path)
    proposal_receipt = case_dir / "proposal.json"
    atomic_write_json(
        proposal_receipt,
        {
            "schema": "skillrace-generated-test-proposal/1",
            "method": "verigrey",
            "phase": "initial_seed",
            "seed_id": seed_id,
            "focus_property_id": focus["property_id"],
            "seed_description": focus["description"],
            "catalog_hash": catalog_hash,
            "prompt_hash": prompt_hash,
            "environment_hash": environment_hash,
            "pi_receipt_path": str(result.receipt_path),
            "pi_receipt_hash": file_hash(result.receipt_path),
            "model": config.model_id,
            "temperature": 1.0,
        },
    )
    proposed = TestCase(
        test_id=f"verigrey-{seed_id}",
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


def initialize_corpus(
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    config: ExperimentConfig,
    output_dir: str | Path,
    *,
    pi_runner: PiRunner = run_pi,
    validator: TestValidator = validate_test,
) -> dict[str, Any]:
    property_ids = [item.get("property_id") for item in properties]
    if (
        not properties
        or not all(
            isinstance(item, dict)
            and isinstance(item.get("property_id"), str)
            and item["property_id"]
            and isinstance(item.get("description"), str)
            and item["description"]
            for item in properties
        )
        or len(set(property_ids)) != len(properties)
    ):
        raise ValueError("properties must contain unique IDs and descriptions")
    output = Path(output_dir)
    output.mkdir(parents=True)
    plan_path = output / "initial-seed-plan.json"
    plan = [
        {
            "seed_id": f"seed-{item['property_id']}",
            "focus_property_id": item["property_id"],
            "description": item["description"],
        }
        for item in properties
    ]
    atomic_write_json(plan_path, plan)
    corpus: list[dict[str, Any]] = []
    seed_replacements: dict[str, int] = {}
    for seed, focus in zip(plan, properties, strict=True):
        case: TestCase | None = None
        for replacement in (1, 2):
            proposed = _materialize_initial_seed(
                seed["seed_id"],
                focus,
                skill,
                properties,
                config,
                output
                / "initial-seeds"
                / seed["seed_id"]
                / f"replacement-{replacement}",
                pi_runner,
                validator,
            )
            seed_replacements[seed["seed_id"]] = replacement
            if proposed.validation_status == "valid":
                case = proposed
                break
        if case is None:
            raise ValueError(
                f"two invalid VeriGrey materializations for {seed['seed_id']}"
            )
        corpus.append(
            {
                **seed,
                "kind": "initial",
                "parent_seed_id": None,
                "test_case": case.to_dict(),
                "status": "pending",
                "tool_sequence": None,
                "novelty_delta": None,
                "energy": None,
                "selected_count": 0,
            }
        )
    catalog_hashes = {
        TestCase.from_dict(seed["test_case"]).nl_check_hash for seed in corpus
    }
    if len(catalog_hashes) != 1:
        raise ValueError("VeriGrey initial seeds do not share one catalog hash")
    corpus_receipt = output / "initial-corpus-receipt.json"
    atomic_write_json(
        corpus_receipt,
        {
            "schema": "skillrace-verigrey-initial-corpus/1",
            "plan_path": str(plan_path),
            "plan_hash": file_hash(plan_path),
            "seed_ids": [seed["seed_id"] for seed in corpus],
            "test_ids": [seed["test_case"]["test_id"] for seed in corpus],
            "seed_replacements": seed_replacements,
            "catalog_hash": next(iter(catalog_hashes)),
            "model": config.model_id,
            "temperature": 1.0,
        },
    )
    return {
        "schema": "skillrace-verigrey-campaign-state/1",
        "phase": "seeding",
        "execution_count": 0,
        "initial_seed_count": len(corpus),
        "corpus": corpus,
        "queue": [],
        "active_seed": None,
        "current_selection": None,
        "coverage": _new_state(),
        "observations": [],
        "initial_corpus_receipt": str(corpus_receipt),
    }


def _assigned_energy(observation: dict[str, Any]) -> int:
    novelty = observation["novelty_delta"]
    score = (
        int(bool(novelty["tools"]))
        + int(bool(novelty["transitions"]))
        + int(bool(novelty["sequence"]))
    )
    return max(1, min(3, score))


def _materialize_mutation(
    parent: dict[str, Any],
    assigned_energy: int,
    mutation_ordinal: int,
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    config: ExperimentConfig,
    output: Path,
    pi_runner: PiRunner,
    validator: TestValidator,
) -> TestCase:
    parent_case = TestCase.from_dict(parent["test_case"])
    parent_prompt = parent_case.prompt_path.read_text(encoding="utf-8")
    parent_dockerfile = (
        parent_case.environment_directory / "Dockerfile"
    ).read_text(encoding="utf-8")
    response: dict[str, str] | None = None
    result: PiResult | None = None
    diagnostic: str | None = None
    for attempt_ordinal in (1, 2):
        attempt = output / f"proposal-attempt-{attempt_ordinal}"
        attempt.mkdir(parents=True)
        correction = (
            f" Your previous response was invalid: {diagnostic}. Return corrected raw "
            "JSON only."
            if diagnostic
            else ""
        )
        prompt_path = attempt / "prompt.txt"
        prompt_path.write_text(
            "Mutate the selected VeriGrey seed into one feasible development task. Use the "
            "seed's observed tool sequence and novelty evidence to seek different agent "
            "behavior while preserving a clear contextual connection to the supplied skill. "
            "The new task must meaningfully exercise the skill and remain compatible with "
            "the complete fixed property catalog. Generate a new visible prompt and complete "
            "Dockerfile. Put all task and artifact paths under /workspace; do not use "
            "/mnt/data or /tmp in the task prompt. The Dockerfile must be no larger than "
            f"32 KiB, start with exactly 'FROM {config.docker_image}', contain exactly one "
            "FROM, use no ADD or COPY, and contain exactly 'WORKDIR /workspace'. Preserve "
            "the installed Pi runtime. Return only one JSON object with exactly prompt and "
            f"dockerfile, both nonempty strings. Do not return checks, IDs, Markdown, or use tools.{correction}\n\n"
            f"SELECTED SEED ID:\n{parent['seed_id']}\n\n"
            f"SELECTED SEED PROMPT:\n{parent_prompt}\n"
            f"SELECTED SEED DOCKERFILE:\n{parent_dockerfile}\n"
            f"SELECTED SEED TOOL SEQUENCE:\n{json.dumps(parent['tool_sequence'], sort_keys=True)}\n\n"
            f"SELECTED SEED NOVELTY:\n{json.dumps(parent['novelty_delta'], sort_keys=True)}\n\n"
            f"ASSIGNED ENERGY:\n{assigned_energy}\n"
            f"MUTATION ORDINAL:\n{mutation_ordinal}\n\n"
            f"COMPLETE FIXED PROPERTIES:\n{json.dumps(properties, sort_keys=True)}\n\n"
            f"SKILL.md:\n{(skill.directory_path / 'SKILL.md').read_text(encoding='utf-8')}\n",
            encoding="utf-8",
        )
        result = pi_runner(
            PiRequest(
                operation_id=(
                    f"proposal.verigrey.mutation.{parent['seed_id']}."
                    f"{mutation_ordinal}.{uuid.uuid4().hex}"
                ),
                provider=config.provider,
                model=config.model_id,
                prompt_path=prompt_path,
                output_dir=attempt,
                image=config.docker_image,
                allowed_tools=("read",),
                max_turns=config.role_budgets["proposer"],
                timeout_seconds=config.timeouts["pi"],
                temperature=1.0,
            )
        )
        if result.status != "completed":
            raise RuntimeError(f"Pi VeriGrey mutation failed: {result.status}")
        try:
            response = _assistant_json(result.trace_path)
            response["dockerfile"] = validate_generated_dockerfile(
                response["dockerfile"], config.docker_image
            )
            break
        except (json.JSONDecodeError, OSError, ValueError) as error:
            diagnostic = str(error)
    if response is None or result is None:
        raise ValueError("two malformed VeriGrey mutation responses")

    test_id = "verigrey-mutation-" + uuid.uuid4().hex
    case_dir = output / test_id
    environment = case_dir / "environment"
    environment.mkdir(parents=True)
    task_path = case_dir / "prompt.txt"
    task_path.write_text(response["prompt"] + "\n", encoding="utf-8")
    checks_path = case_dir / "nl_checks.json"
    atomic_write_json(checks_path, properties)
    (environment / "Dockerfile").write_text(response["dockerfile"], encoding="utf-8")
    atomic_write_json(environment / "sanity.json", {"status": "pass"})
    prompt_hash = file_hash(task_path)
    environment_hash = tree_hash(environment)
    catalog_hash = file_hash(checks_path)
    proposal_receipt = case_dir / "proposal.json"
    atomic_write_json(
        proposal_receipt,
        {
            "schema": "skillrace-generated-test-proposal/1",
            "method": "verigrey",
            "phase": "mutation",
            "parent_seed_id": parent["seed_id"],
            "parent_test_id": parent_case.test_id,
            "parent_prompt_hash": parent_case.prompt_hash,
            "parent_environment_hash": parent_case.environment_hash,
            "parent_tool_sequence": parent["tool_sequence"],
            "parent_novelty_delta": parent["novelty_delta"],
            "assigned_energy": assigned_energy,
            "mutation_ordinal": mutation_ordinal,
            "catalog_hash": catalog_hash,
            "prompt_hash": prompt_hash,
            "environment_hash": environment_hash,
            "pi_receipt_path": str(result.receipt_path),
            "pi_receipt_hash": file_hash(result.receipt_path),
            "model": config.model_id,
            "temperature": 1.0,
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


def select_test(
    state: dict[str, Any],
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    config: ExperimentConfig,
    output_dir: str | Path,
    *,
    pi_runner: PiRunner = run_pi,
    validator: TestValidator = validate_test,
) -> TestCase:
    if state.get("schema") != "skillrace-verigrey-campaign-state/1":
        raise ValueError("VeriGrey campaign state is invalid")
    if state.get("execution_count", 0) >= config.iteration_budget:
        raise ValueError("VeriGrey execution budget exhausted")
    if state.get("current_selection") is not None:
        raise ValueError("VeriGrey selection has not been observed")
    if state.get("phase") == "seeding":
        pending = next(
            (seed for seed in state["corpus"] if seed.get("status") == "pending"),
            None,
        )
        if pending is None:
            raise ValueError("VeriGrey has no pending initial seed")
        case = TestCase.from_dict(pending["test_case"])
        pending["selected_count"] += 1
        state["current_selection"] = {
            "phase": "initial_seed",
            "seed_id": pending["seed_id"],
            "test_id": case.test_id,
        }
        return case
    if state.get("phase") != "mutation":
        raise ValueError("VeriGrey campaign phase is invalid")

    active = state.get("active_seed")
    starting_parent = active is None
    if starting_parent:
        if not state["queue"]:
            raise ValueError("VeriGrey seed queue is empty")
        parent_id = state["queue"][0]
        parent = next(item for item in state["corpus"] if item["seed_id"] == parent_id)
        energy_total = parent["energy"]
        energy_remaining = energy_total
    else:
        parent_id = active["seed_id"]
        parent = next(item for item in state["corpus"] if item["seed_id"] == parent_id)
        energy_total = active["energy_total"]
        energy_remaining = active["energy_remaining"]
    mutation_ordinal = energy_total - energy_remaining + 1
    case = _materialize_mutation(
        parent,
        energy_total,
        mutation_ordinal,
        skill,
        properties,
        config,
        Path(output_dir),
        pi_runner,
        validator,
    )
    if case.validation_status == "invalid_test":
        return case
    if starting_parent:
        state["queue"].pop(0)
        state["active_seed"] = {
            "seed_id": parent_id,
            "energy_total": energy_total,
            "energy_remaining": energy_remaining,
        }
    parent["selected_count"] += 1
    state["current_selection"] = {
        "phase": "mutation",
        "parent_seed_id": parent_id,
        "test_id": case.test_id,
        "test_case": case.to_dict(),
        "assigned_energy": energy_total,
        "mutation_ordinal": mutation_ordinal,
    }
    return case


def observe_execution(
    state: dict[str, Any],
    sequence: list[dict[str, Any]],
) -> dict[str, Any]:
    if state.get("schema") != "skillrace-verigrey-campaign-state/1":
        raise ValueError("VeriGrey campaign state is invalid")
    selection = state.get("current_selection")
    if not isinstance(selection, dict):
        raise ValueError("VeriGrey execution has no current selection")
    updated = copy.deepcopy(state)
    coverage = update_state(updated["coverage"], sequence)
    observation = coverage["last_observation"]
    updated["coverage"] = coverage
    updated["execution_count"] += 1
    if selection.get("phase") == "initial_seed":
        seed = next(
            item
            for item in updated["corpus"]
            if item["seed_id"] == selection["seed_id"]
        )
        seed["status"] = "executed"
        seed["tool_sequence"] = copy.deepcopy(sequence)
        seed["novelty_delta"] = copy.deepcopy(observation["novelty_delta"])
        seed["energy"] = _assigned_energy(observation)
        updated["observations"].append(
            {
                "execution": updated["execution_count"],
                "phase": "initial_seed",
                "seed_id": seed["seed_id"],
                "test_id": selection["test_id"],
                "tool_sequence": copy.deepcopy(sequence),
                "novelty_delta": copy.deepcopy(observation["novelty_delta"]),
                "assigned_energy": seed["energy"],
                "corpus_admitted": True,
            }
        )
    elif selection.get("phase") == "mutation":
        parent = next(
            item
            for item in updated["corpus"]
            if item["seed_id"] == selection["parent_seed_id"]
        )
        novelty = observation["novelty_delta"]
        admitted = bool(
            novelty["tools"] or novelty["transitions"] or novelty["sequence"]
        )
        offspring_id: str | None = None
        if admitted:
            offspring_id = "offspring-" + selection["test_id"]
            updated["corpus"].append(
                {
                    "seed_id": offspring_id,
                    "focus_property_id": parent["focus_property_id"],
                    "description": parent["description"],
                    "kind": "offspring",
                    "parent_seed_id": parent["seed_id"],
                    "test_case": selection["test_case"],
                    "status": "executed",
                    "tool_sequence": copy.deepcopy(sequence),
                    "novelty_delta": copy.deepcopy(novelty),
                    "energy": _assigned_energy(observation),
                    "selected_count": 0,
                }
            )
            updated["queue"].append(offspring_id)
        active = updated["active_seed"]
        active["energy_remaining"] -= 1
        if active["energy_remaining"] == 0:
            updated["queue"].append(active["seed_id"])
            updated["active_seed"] = None
        updated["observations"].append(
            {
                "execution": updated["execution_count"],
                "phase": "mutation",
                "parent_seed_id": parent["seed_id"],
                "offspring_seed_id": offspring_id,
                "test_id": selection["test_id"],
                "mutation_ordinal": selection["mutation_ordinal"],
                "assigned_energy": selection["assigned_energy"],
                "tool_sequence": copy.deepcopy(sequence),
                "novelty_delta": copy.deepcopy(novelty),
                "corpus_admitted": admitted,
            }
        )
    else:
        raise ValueError("VeriGrey selection phase is invalid")
    updated["current_selection"] = None
    if updated["phase"] == "seeding" and all(
        item["status"] == "executed" for item in updated["corpus"]
    ):
        updated["phase"] = "mutation"
        updated["queue"] = [item["seed_id"] for item in updated["corpus"]]
    return updated
