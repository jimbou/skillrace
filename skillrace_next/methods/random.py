import json
from pathlib import Path
from typing import Any, Callable
import uuid

from ..pipeline.stages import validate_test
from ..records import ExperimentConfig, SkillVersion, TestCase
from ..runtime.pi import PiRequest, PiResult, run_pi
from ..storage import atomic_write_json, file_hash, tree_hash


PiRunner = Callable[[PiRequest], PiResult]
Proposer = Callable[..., TestCase]
Validator = Callable[[TestCase, ExperimentConfig], TestCase]


def _assistant_json(trace_path: Path) -> dict[str, Any]:
    assistant_texts: list[str] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message", {})
        if message.get("role") != "assistant":
            continue
        texts = [
            item.get("text", "")
            for item in message.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if texts:
            assistant_texts.append("".join(texts))
    if not assistant_texts:
        raise ValueError("proposal trace contains no assistant JSON")
    response = assistant_texts[-1].strip()
    if (
        response.startswith("```json\n")
        and response.endswith("\n```")
        and response.count("```") == 2
    ):
        response = response[len("```json\n") : -len("\n```")]
    value = json.loads(response)
    if not isinstance(value, dict) or set(value) != {"prompt", "property_ids"}:
        raise ValueError("proposal must contain exactly prompt and property_ids")
    prompt = value["prompt"]
    property_ids = value["property_ids"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("proposal prompt must be nonempty")
    if (
        not isinstance(property_ids, list)
        or not property_ids
        or not all(isinstance(item, str) for item in property_ids)
        or len(set(property_ids)) != len(property_ids)
    ):
        raise ValueError("proposal property_ids must be a nonempty unique string list")
    return {"prompt": prompt.strip(), "property_ids": property_ids}


def _proposal_prompt(
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    diagnostic: str | None = None,
) -> str:
    skill_text = (skill.directory_path / "SKILL.md").read_text(encoding="utf-8")
    correction = (
        f"\nYour previous response was malformed: {diagnostic}. Correct only the format."
        if diagnostic
        else ""
    )
    return (
        "Propose one independent development test for this skill. Return only one JSON "
        "object with exactly two keys: prompt (a nonempty task string) and property_ids "
        "(a nonempty list chosen only from the supplied property IDs). Do not use tools.\n\n"
        f"SKILL.md:\n{skill_text}\n\n"
        f"Properties:\n{json.dumps(properties, sort_keys=True)}"
        f"{correction}\n"
    )


def propose_test(
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    config: ExperimentConfig,
    output_dir: str | Path,
    pi_runner: PiRunner = run_pi,
) -> TestCase:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    known = {
        item["property_id"]: dict(item)
        for item in properties
        if isinstance(item, dict) and isinstance(item.get("property_id"), str)
    }
    if not known:
        raise ValueError("properties must contain at least one property_id")
    parsed: dict[str, Any] | None = None
    receipt_path: Path | None = None
    diagnostic: str | None = None
    for ordinal in (1, 2):
        attempt = output / f"proposal-attempt-{ordinal}"
        attempt.mkdir()
        prompt_path = attempt / "prompt.txt"
        prompt_path.write_text(
            _proposal_prompt(skill, properties, diagnostic), encoding="utf-8"
        )
        suffix = "" if ordinal == 1 else ".correction"
        result = pi_runner(
            PiRequest(
                operation_id=f"proposal.random.{uuid.uuid4().hex}{suffix}",
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
        receipt_path = result.receipt_path
        if result.status != "completed":
            raise RuntimeError(f"Pi proposal failed: {result.status}")
        try:
            parsed = _assistant_json(result.trace_path)
            if any(property_id not in known for property_id in parsed["property_ids"]):
                raise ValueError("proposal referenced an unknown property ID")
            break
        except (json.JSONDecodeError, OSError, ValueError) as error:
            diagnostic = str(error)
    if parsed is None or receipt_path is None:
        raise ValueError("two malformed proposal responses")

    test_id = "random-" + uuid.uuid4().hex
    case = output / test_id
    environment = case / "environment"
    environment.mkdir(parents=True)
    prompt_path = case / "prompt.txt"
    prompt_path.write_text(parsed["prompt"] + "\n", encoding="utf-8")
    nl_check_path = case / "nl_checks.json"
    selected = [known[property_id] for property_id in parsed["property_ids"]]
    atomic_write_json(nl_check_path, selected)
    if "\n" in config.docker_image:
        raise ValueError("docker image must not contain a newline")
    (environment / "Dockerfile").write_text(
        f"FROM {config.docker_image}\nWORKDIR /workspace\n", encoding="utf-8"
    )
    atomic_write_json(environment / "sanity.json", {"status": "pass"})
    return TestCase(
        test_id=test_id,
        prompt_path=prompt_path,
        prompt_hash=file_hash(prompt_path),
        environment_directory=environment,
        environment_hash=tree_hash(environment),
        nl_check_path=nl_check_path,
        nl_check_hash=file_hash(nl_check_path),
        origin_method="random",
        proposal_receipt=receipt_path,
        validation_status="pending",
        validation_diagnostic="",
        container_image_id="",
    )


def propose_valid_test(
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    config: ExperimentConfig,
    output_dir: str | Path,
    *,
    proposer: Proposer = propose_test,
    validator: Validator = validate_test,
) -> TestCase:
    output = Path(output_dir)
    last: TestCase | None = None
    for ordinal in (1, 2):
        proposed = proposer(
            skill,
            properties,
            config,
            output / f"replacement-{ordinal}",
        )
        last = validator(proposed, config)
        if last.validation_status == "valid":
            return last
    if last is None:
        raise RuntimeError("proposal loop did not run")
    return last
