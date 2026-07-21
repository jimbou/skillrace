import json
from pathlib import Path
from typing import Any, Callable
import uuid

from ..pipeline.stages import validate_generated_dockerfile, validate_test
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
    ):
        response = response[len("```json\n") : -len("\n```")]
    value = json.loads(response)
    if not isinstance(value, dict) or set(value) != {"prompt", "dockerfile"}:
        raise ValueError("proposal must contain exactly prompt and dockerfile")
    prompt = value["prompt"]
    dockerfile = value["dockerfile"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("proposal prompt must be nonempty")
    if not isinstance(dockerfile, str) or not dockerfile.strip():
        raise ValueError("proposal Dockerfile must be nonempty")
    return {"prompt": prompt.strip(), "dockerfile": dockerfile}


def _proposal_prompt(
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    base_image: str,
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
        "object with exactly two keys: prompt (a nonempty task string) and dockerfile "
        "(the complete Dockerfile string for the task). Do not return check prose, check "
        "IDs, or any other keys. The task container must meaningfully exercise the supplied "
        "skill and be compatible with the complete fixed property catalog. A "
        "generic task that merely uses convenient tools is invalid; tool use is not a "
        "substitute for skill relevance. Make all inline data, expected values, examples, "
        "and prose internally consistent; do not emit mutually inconsistent requirements. "
        "The Dockerfile may create task inputs, and the visible prompt must accurately "
        "describe any files or environment conditions it creates. Put every task and "
        "artifact path under /workspace. Do not use /mnt/data or /tmp in the task prompt. "
        "The Dockerfile must be no larger than 32 KiB, start with exactly "
        f"'FROM {base_image}', contain exactly one FROM, use no ADD or COPY, "
        "and contain exactly 'WORKDIR /workspace'. Preserve the installed Pi runtime. "
        "All fixed properties must be consistent with requirements visible in the task "
        "prompt. Do not use Markdown fences or tools.\n\n"
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
    if not known or len(known) != len(properties):
        raise ValueError("properties must contain unique property IDs")
    parsed: dict[str, Any] | None = None
    pi_receipt_path: Path | None = None
    diagnostic: str | None = None
    for ordinal in (1, 2):
        attempt = output / f"proposal-attempt-{ordinal}"
        attempt.mkdir()
        prompt_path = attempt / "prompt.txt"
        prompt_path.write_text(
            _proposal_prompt(skill, properties, config.docker_image, diagnostic),
            encoding="utf-8",
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
                temperature=1.0,
            )
        )
        pi_receipt_path = result.receipt_path
        if result.status != "completed":
            raise RuntimeError(f"Pi proposal failed: {result.status}")
        try:
            parsed = _assistant_json(result.trace_path)
            parsed["dockerfile"] = validate_generated_dockerfile(
                parsed["dockerfile"], config.docker_image
            )
            break
        except (json.JSONDecodeError, OSError, ValueError) as error:
            diagnostic = str(error)
    if parsed is None or pi_receipt_path is None:
        raise ValueError("two malformed proposal responses")

    test_id = "random-" + uuid.uuid4().hex
    case = output / test_id
    environment = case / "environment"
    environment.mkdir(parents=True)
    prompt_path = case / "prompt.txt"
    prompt_path.write_text(parsed["prompt"] + "\n", encoding="utf-8")
    nl_check_path = case / "nl_checks.json"
    atomic_write_json(nl_check_path, list(known.values()))
    (environment / "Dockerfile").write_text(
        parsed["dockerfile"], encoding="utf-8"
    )
    atomic_write_json(environment / "sanity.json", {"status": "pass"})
    prompt_hash = file_hash(prompt_path)
    environment_hash = tree_hash(environment)
    catalog_hash = file_hash(nl_check_path)
    proposal_receipt = case / "proposal.json"
    atomic_write_json(
        proposal_receipt,
        {
            "schema": "skillrace-generated-test-proposal/1",
            "method": "random",
            "independent": True,
            "catalog_hash": catalog_hash,
            "prompt_hash": prompt_hash,
            "environment_hash": environment_hash,
            "pi_receipt_path": str(pi_receipt_path),
            "pi_receipt_hash": file_hash(pi_receipt_path),
            "model": config.model_id,
            "temperature": 1.0,
        },
    )
    return TestCase(
        test_id=test_id,
        prompt_path=prompt_path,
        prompt_hash=prompt_hash,
        environment_directory=environment,
        environment_hash=environment_hash,
        nl_check_path=nl_check_path,
        nl_check_hash=catalog_hash,
        origin_method="random",
        proposal_receipt=proposal_receipt,
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
