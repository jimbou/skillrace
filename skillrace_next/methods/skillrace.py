import json
from pathlib import Path
import re
from typing import Any, Callable
import uuid

from .branch_view import build_edge_index, compact_branch_for_prompt, isolate_branch
from .reasoning_tree import validate_tree
from ..pipeline.stages import validate_generated_dockerfile, validate_test
from ..records import ExperimentConfig, SkillVersion, TestCase
from ..runtime.pi import PiRequest, PiResult, run_pi
from ..storage import atomic_write_json, file_hash, tree_hash
from ..study_images import capability_for_image


PiRunner = Callable[[PiRequest], PiResult]
TestValidator = Callable[[TestCase, ExperimentConfig], TestCase]
def _assistant_text(trace_path: Path) -> str:
    responses: list[str] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message", {})
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
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
        raise ValueError("episode response contains no assistant JSON")
    return responses[-1].strip()


def _assistant_json(trace_path: Path) -> Any:
    response = _assistant_text(trace_path)
    if (
        response.startswith("```json\n")
        and response.endswith("```")
    ):
        response = response[len("```json\n") : -len("```")].strip()
    return json.loads(response)


def _selector_json(trace_path: Path) -> Any:
    response = _assistant_text(trace_path)
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        if response.count("```json") != 1 or not response.rstrip().endswith("```"):
            raise
        start = response.index("```json") + len("```json")
        end = response.rfind("```")
        return json.loads(response[start:end].strip())


def create_diversity_plan(
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    config: ExperimentConfig,
    output_dir: str | Path,
    *,
    pi_runner: PiRunner = run_pi,
) -> dict[str, Any]:
    property_ids = [item.get("property_id") for item in properties]
    if (
        not properties
        or not all(isinstance(item, str) and item for item in property_ids)
        or len(set(property_ids)) != len(properties)
    ):
        raise ValueError("properties must contain unique property IDs")
    output = Path(output_dir)
    output.mkdir(parents=True)
    catalog_path = output / "properties.json"
    atomic_write_json(catalog_path, properties)
    diagnostic: str | None = None
    descriptions: list[dict[str, str]] | None = None
    result: PiResult | None = None
    capability = capability_for_image(config.docker_image)
    for ordinal in (1, 2, 3):
        attempt = output / f"plan-attempt-{ordinal}"
        attempt.mkdir()
        correction = (
            f" Your previous response was invalid: {diagnostic}. Return corrected raw "
            "JSON only."
            if diagnostic
            else ""
        )
        prompt_path = attempt / "prompt.txt"
        prompt_path.write_text(
            "Design exactly ten semantically diverse high-level development-test "
            "descriptions for the supplied skill. Consider the complete fixed property "
            "catalog while maximizing diversity across tasks and Docker-environment "
            "conditions. Every description must be feasible: the requested task must be "
            "finishable in its stated environment within the fixed agent budget. A strong "
            f"agent must finish implementation and verification in at most "
            f"{config.role_budgets.get('weak_agent', 4)} Pi turns and "
            f"{config.timeouts['pi']} seconds. Ask for one focused behavior, not a broad "
            "application, server, multi-feature library, or accompanying test suite unless "
            "the supplied skill specifically requires it. Do not "
            "make a required dependency unavailable without a concrete local recovery path, "
            "require unavailable credentials or services, or use sheer task size as the "
            "challenge. Do not require a huge preloaded input when a small representative "
            "fixture exercises the same behavior. The weak agent has no Docker access. "
            "It may install additional packages online as root, but installation consumes "
            "the unchanged task budget. Required dependencies may be installed by the "
            "generated Dockerfile or by the agent when feasible within that budget. Keep "
            "environment conditions limited to pre-existing inputs and dependencies; they "
            "must not contain the requested solution or claim that it is already embedded. "
            "Keep requested artifact destinations under /workspace. Do not use /mnt/data "
            "or /tmp in the intended task or environment conditions because the frozen "
            "description must be materializable under the generated-task path contract. HTTP routes "
            "and system paths used only as inputs or repair conditions may be described "
            "normally. Return only one JSON array of exactly ten objects. Every object "
            "must contain exactly task and environment_conditions, both nonempty strings. "
            "Descriptions are planning inputs, not executable tests. Do not emit property "
            "or check IDs, prompts, Dockerfiles, prose outside the array, or use tools."
            f"{correction}\n\n"
            f"BASE IMAGE CAPABILITIES:\n{capability.text}\n\n"
            f"FIXED PROPERTIES:\n{json.dumps(properties, sort_keys=True)}\n\n"
            f"SKILL.md:\n{(skill.directory_path / 'SKILL.md').read_text(encoding='utf-8')}\n",
            encoding="utf-8",
        )
        result = pi_runner(
            PiRequest(
                operation_id=f"proposal.skillrace.plan.{uuid.uuid4().hex}",
                provider=config.provider,
                model=config.model_id,
                prompt_path=prompt_path,
                output_dir=attempt,
                image=config.docker_image,
                allowed_tools=("read",),
                max_turns=config.role_budgets["proposer"],
                timeout_seconds=config.timeouts["provider"],
                temperature=1.0,
            )
        )
        if result.status != "completed":
            raise RuntimeError(f"Pi SkillRACE diversity plan failed: {result.status}")
        try:
            parsed = _assistant_json(result.trace_path)
            if (
                not isinstance(parsed, list)
                or len(parsed) != 10
                or any(
                    not isinstance(item, dict)
                    or set(item) != {"task", "environment_conditions"}
                    or not all(
                        isinstance(item[name], str) and item[name].strip()
                        for name in ("task", "environment_conditions")
                    )
                    for item in parsed
                )
            ):
                raise ValueError("SkillRACE diversity plan is invalid")
            normalized = [
                {
                    "task": item["task"].strip(),
                    "environment_conditions": item[
                        "environment_conditions"
                    ].strip(),
                }
                for item in parsed
            ]
            if len({json.dumps(item, sort_keys=True) for item in normalized}) != 10:
                raise ValueError("SkillRACE diversity plan contains duplicates")
            external_path = next(
                (
                    path
                    for path in ("/mnt/data", "/tmp")
                    if any(
                        path in item[field]
                        for item in normalized
                        for field in ("task", "environment_conditions")
                    )
                ),
                None,
            )
            if external_path is not None:
                raise ValueError(
                    "generated plan path is outside /workspace: " + external_path
                )
            descriptions = normalized
            break
        except (json.JSONDecodeError, ValueError) as error:
            diagnostic = str(error)
    if descriptions is None or result is None:
        raise ValueError("three invalid SkillRACE diversity plans")
    frozen = [
        {"seed_id": f"seed-{index:02d}", **item}
        for index, item in enumerate(descriptions, 1)
    ]
    plan_path = output / "diversity-plan.json"
    atomic_write_json(plan_path, frozen)
    plan_hash = file_hash(plan_path)
    catalog_hash = file_hash(catalog_path)
    receipt_path = output / "diversity-plan-receipt.json"
    atomic_write_json(
        receipt_path,
        {
            "schema": "skillrace-diversity-plan-receipt/1",
            "plan_path": str(plan_path),
            "plan_hash": plan_hash,
            "catalog_path": str(catalog_path),
            "catalog_hash": catalog_hash,
            "description_count": 10,
            "pi_receipt_path": str(result.receipt_path),
            "pi_receipt_hash": file_hash(result.receipt_path),
            "model": config.model_id,
            "temperature": 1.0,
            "capability_manifest_hash": capability.manifest_hash,
        },
    )
    return {
        "schema": "skillrace-diversity-plan/1",
        "descriptions": frozen,
        "plan_path": str(plan_path),
        "plan_hash": plan_hash,
        "catalog_hash": catalog_hash,
        "receipt_path": str(receipt_path),
    }


def materialize_initial_test(
    plan: dict[str, Any],
    description_index: int,
    skill: SkillVersion,
    properties: list[dict[str, Any]],
    config: ExperimentConfig,
    output_dir: str | Path,
    *,
    pi_runner: PiRunner = run_pi,
    validator: TestValidator = validate_test,
) -> TestCase:
    if plan.get("schema") != "skillrace-diversity-plan/1":
        raise ValueError("SkillRACE diversity plan is invalid")
    plan_path = Path(plan["plan_path"])
    if file_hash(plan_path) != plan.get("plan_hash"):
        raise ValueError("SkillRACE diversity plan hash differs")
    descriptions = plan.get("descriptions")
    if (
        not isinstance(description_index, int)
        or not isinstance(descriptions, list)
        or description_index < 0
        or description_index >= len(descriptions)
    ):
        raise ValueError("SkillRACE description index is invalid")
    description = descriptions[description_index]
    seed_id = description["seed_id"]
    output = Path(output_dir)
    output.mkdir(parents=True)
    diagnostic: str | None = None
    last: TestCase | None = None
    last_result: PiResult | None = None
    capability = capability_for_image(config.docker_image)
    for replacement in (1, 2, 3):
        attempt = output / f"replacement-{replacement}"
        pi_output = attempt / "pi"
        pi_output.mkdir(parents=True)
        correction = (
            f" Your previous materialization was invalid: {diagnostic}. Generate a fresh "
            "corrected test. Recheck every Dockerfile constraint before responding."
            if diagnostic
            else ""
        )
        prompt_path = pi_output / "prompt.txt"
        prompt_path.write_text(
            "Materialize the selected frozen SkillRACE description into one feasible "
            "development test. Generate its visible task prompt and complete Dockerfile. "
            "The description guides task and environment diversity but does not replace the "
            "complete fixed property catalog. The prompt and Dockerfile must not contradict "
            "the frozen environment conditions. Keep the concrete task finishable, including "
            f"verification, in at most {config.role_budgets.get('weak_agent', 4)} Pi turns "
            f"and {config.timeouts['pi']} seconds. The root task agent may install "
            "additional packages online, but installation consumes the unchanged task "
            "budget. Any required dependency must exist in the resulting image or be "
            "installable within that budget. The task must meaningfully exercise the skill "
            "and remain compatible with every property. The Dockerfile may provide inputs and "
            "dependencies but must not create or test the requested solution. Put all task "
            "inputs in the image compactly: if repetitive fixture data is required, generate "
            "it compactly instead of enumerating thousands of values. Put all task "
            "and artifact paths under /workspace; do not use /mnt/data or /tmp in the task "
            "prompt. The Dockerfile must "
            "be no larger than 32 KiB, start with exactly "
            f"'FROM {config.docker_image}', contain exactly one FROM, use no ADD or COPY, "
            "and contain exactly 'WORKDIR /workspace'. Preserve the installed Pi runtime. "
            "Return only one JSON object with exactly prompt and dockerfile, both nonempty "
            f"strings. Do not return checks, IDs, Markdown, or use tools.{correction}\n\n"
            f"BASE IMAGE CAPABILITIES:\n{capability.text}\n\n"
            f"FROZEN DESCRIPTION:\n{json.dumps(description, sort_keys=True)}\n\n"
            f"COMPLETE FIXED PROPERTIES:\n{json.dumps(properties, sort_keys=True)}\n\n"
            f"SKILL.md:\n{(skill.directory_path / 'SKILL.md').read_text(encoding='utf-8')}\n",
            encoding="utf-8",
        )
        result = pi_runner(
            PiRequest(
                operation_id=(
                    f"proposal.skillrace.seed.{seed_id}.{replacement}."
                    f"{uuid.uuid4().hex}"
                ),
                provider=config.provider,
                model=config.model_id,
                prompt_path=prompt_path,
                output_dir=pi_output,
                image=config.docker_image,
                allowed_tools=("read",),
                max_turns=config.role_budgets["proposer"],
                timeout_seconds=config.timeouts["provider"],
                temperature=1.0,
            )
        )
        last_result = result
        if result.status != "completed":
            raise RuntimeError(f"Pi SkillRACE seed materialization failed: {result.status}")
        try:
            response = _assistant_json(result.trace_path)
            if not isinstance(response, dict) or set(response) != {
                "prompt",
                "dockerfile",
            }:
                raise ValueError("SkillRACE seed response is invalid")
            prompt = response["prompt"]
            if (
                not isinstance(prompt, str)
                or not prompt.strip()
                or not isinstance(response["dockerfile"], str)
                or not response["dockerfile"].strip()
            ):
                raise ValueError("SkillRACE seed fields must be nonempty")
            dockerfile = validate_generated_dockerfile(
                response["dockerfile"], config.docker_image
            )
        except (json.JSONDecodeError, OSError, ValueError) as error:
            diagnostic = str(error)
            continue
        test_id = f"skillrace-{seed_id}-" + uuid.uuid4().hex
        case_dir = attempt / test_id
        environment = case_dir / "environment"
        environment.mkdir(parents=True)
        task_path = case_dir / "prompt.txt"
        task_path.write_text(prompt.strip() + "\n", encoding="utf-8")
        checks_path = case_dir / "nl_checks.json"
        atomic_write_json(checks_path, properties)
        (environment / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        atomic_write_json(environment / "sanity.json", {"status": "pass"})
        prompt_hash = file_hash(task_path)
        environment_hash = tree_hash(environment)
        catalog_hash = file_hash(checks_path)
        proposal_receipt = case_dir / "proposal.json"
        atomic_write_json(
            proposal_receipt,
            {
                "schema": "skillrace-generated-test-proposal/1",
                "method": "skillrace",
                "phase": "initial_seed",
                "seed_id": seed_id,
                "seed_index": description_index + 1,
                "description": description,
                "plan_hash": plan["plan_hash"],
                "catalog_hash": catalog_hash,
                "prompt_hash": prompt_hash,
                "environment_hash": environment_hash,
                "pi_receipt_path": str(result.receipt_path),
                "pi_receipt_hash": file_hash(result.receipt_path),
                "model": config.model_id,
                "temperature": 1.0,
                "capability_manifest_hash": capability.manifest_hash,
            },
        )
        pending = TestCase(
            test_id=test_id,
            prompt_path=task_path,
            prompt_hash=prompt_hash,
            environment_directory=environment,
            environment_hash=environment_hash,
            nl_check_path=checks_path,
            nl_check_hash=catalog_hash,
            origin_method="skillrace",
            proposal_receipt=proposal_receipt,
            validation_status="pending",
            validation_diagnostic="",
            container_image_id="",
        )
        last = validator(pending, config)
        if last.validation_status == "valid":
            return last
        diagnostic = last.validation_diagnostic
    if last is not None:
        return last
    if last_result is None:
        raise RuntimeError("SkillRACE seed materialization loop did not run")
    failure_diagnostic = diagnostic or "SkillRACE seed response is invalid"
    test_id = f"skillrace-{seed_id}-invalid-" + uuid.uuid4().hex
    case_dir = output / test_id
    environment = case_dir / "environment"
    environment.mkdir(parents=True)
    task_path = case_dir / "prompt.txt"
    task_path.write_text(
        "Invalid SkillRACE materialization; do not execute.\n",
        encoding="utf-8",
    )
    checks_path = case_dir / "nl_checks.json"
    atomic_write_json(checks_path, properties)
    (environment / "Dockerfile").write_text(
        f"FROM {config.docker_image}\nWORKDIR /workspace\n",
        encoding="utf-8",
    )
    atomic_write_json(
        environment / "sanity.json",
        {"status": "fail", "diagnostic": failure_diagnostic},
    )
    prompt_hash = file_hash(task_path)
    environment_hash = tree_hash(environment)
    catalog_hash = file_hash(checks_path)
    proposal_receipt = case_dir / "proposal.json"
    atomic_write_json(
        proposal_receipt,
        {
            "schema": "skillrace-generated-test-proposal/1",
            "method": "skillrace",
            "phase": "initial_seed",
            "seed_id": seed_id,
            "seed_index": description_index + 1,
            "description": description,
            "plan_hash": plan["plan_hash"],
            "catalog_hash": catalog_hash,
            "prompt_hash": prompt_hash,
            "environment_hash": environment_hash,
            "pi_receipt_path": str(last_result.receipt_path),
            "pi_receipt_hash": file_hash(last_result.receipt_path),
            "model": config.model_id,
            "temperature": 1.0,
            "status": "invalid_test",
            "diagnostic": failure_diagnostic,
        },
    )
    return TestCase(
        test_id=test_id,
        prompt_path=task_path,
        prompt_hash=prompt_hash,
        environment_directory=environment,
        environment_hash=environment_hash,
        nl_check_path=checks_path,
        nl_check_hash=catalog_hash,
        origin_method="skillrace",
        proposal_receipt=proposal_receipt,
        validation_status="invalid_test",
        validation_diagnostic=failure_diagnostic,
        container_image_id="",
    )


def propose_test(
    tree: dict[str, Any],
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
    validated_tree = validate_tree(tree)
    edge_index = build_edge_index(validated_tree)
    known_edge_ids = {item["edge_id"] for item in edge_index}
    if not known_edge_ids:
        raise ValueError("SkillRACE tree contains no observed reasoning edges")
    output = config.output_root / "skillrace-proposals" / uuid.uuid4().hex
    output.mkdir(parents=True)
    selector_input = output / "selector-input"
    selector_input.mkdir()
    atomic_write_json(selector_input / "tree.json", validated_tree)
    atomic_write_json(selector_input / "edge-index.json", edge_index)
    skill_text = (skill.directory_path / "SKILL.md").read_text(encoding="utf-8")
    capability = capability_for_image(config.docker_image)
    selector_diagnostic: str | None = None
    selector_result: PiResult | None = None
    target_edge_id: str | None = None
    selection_reason: str | None = None
    for ordinal in (1, 2, 3):
        selector_output = output / f"selector-attempt-{ordinal}"
        selector_output.mkdir()
        selector_prompt = selector_output / "prompt.txt"
        correction = (
            f" Your previous response was invalid: {selector_diagnostic}. Return corrected "
            "raw JSON only."
            if selector_diagnostic
            else ""
        )
        selector_prompt.write_text(
            "Act as the SkillRACE edge selector. Choose exactly one real observed reasoning "
            "edge from the COMPACT EDGE INDEX below. Prefer the edge whose assumption has the "
            "best chance of exposing a genuine, patchable skill failure under the fixed checks. "
            "previous_outcomes are observations immediately before the displayed reasoning "
            "edge; use them as guard evidence, not as the target node's identity. "
            "The resulting task must remain achievable within the unchanged agent budget when "
            "the skill gives the right guidance; do not select sheer difficulty, impossible "
            "requirements, unavailable credentials, or an environment condition without a "
            "concrete local recovery route. Return only one JSON object with exactly "
            "target_edge_id and selection_reason, both nonempty strings. The edge ID must be "
            f"copied exactly from the index. Do not use tools.{correction}\n\n"
            f"FIXED PROPERTIES:\n{json.dumps(properties, sort_keys=True)}\n\n"
            f"SKILL.md:\n{skill_text}\n\n"
            f"COMPACT EDGE INDEX:\n{json.dumps(edge_index, sort_keys=True)}\n",
            encoding="utf-8",
        )
        selector_result = pi_runner(
            PiRequest(
                operation_id=f"proposal.skillrace.select.{uuid.uuid4().hex}",
                provider=config.provider,
                model=config.model_id,
                prompt_path=selector_prompt,
                output_dir=selector_output,
                image=config.docker_image,
                allowed_tools=(),
                max_turns=min(2, config.role_budgets["proposer"]),
                timeout_seconds=config.timeouts["provider"],
                temperature=1.0,
            )
        )
        if selector_result.status != "completed":
            raise RuntimeError(
                f"Pi SkillRACE edge selection failed: {selector_result.status}"
            )
        try:
            selection = _selector_json(selector_result.trace_path)
            if not isinstance(selection, dict) or set(selection) != {
                "target_edge_id",
                "selection_reason",
            }:
                raise ValueError("SkillRACE edge selection response is invalid")
            if not all(
                isinstance(selection[name], str) and selection[name].strip()
                for name in ("target_edge_id", "selection_reason")
            ):
                raise ValueError("SkillRACE edge selection fields must be nonempty")
            target_edge_id = selection["target_edge_id"].strip()
            selection_reason = selection["selection_reason"].strip()
            if target_edge_id not in known_edge_ids:
                raise ValueError("SkillRACE edge selector selected an unknown edge")
        except (json.JSONDecodeError, ValueError) as error:
            selector_diagnostic = str(error)
            if ordinal < 3:
                continue
            raise ValueError("three invalid SkillRACE edge selections") from error
        break
    if selector_result is None or target_edge_id is None or selection_reason is None:
        raise RuntimeError("SkillRACE edge selection loop did not return")

    selected_branch = isolate_branch(validated_tree, target_edge_id)
    atomic_write_json(selector_input / "selected-branch.json", selected_branch)
    prompt_branch = compact_branch_for_prompt(selected_branch)
    selector_input_hash = tree_hash(selector_input)

    response_fields = {
        "bug_hypothesis",
        "mutation",
        "why_patchable",
        "prompt",
        "dockerfile",
    }
    mutator_diagnostic: str | None = None
    last: TestCase | None = None
    for ordinal in (1, 2, 3, 4):
        mutator_output = output / f"mutator-attempt-{ordinal}"
        mutator_output.mkdir()
        mutator_prompt = mutator_output / "prompt.txt"
        correction = (
            f" Your previous generated test was invalid: {mutator_diagnostic}. Generate "
            "a corrected test for the same selected edge."
            if mutator_diagnostic
            else ""
        )
        mutator_prompt.write_text(
            "Act as the SkillRACE test mutator. Use the ISOLATED OBSERVED BRANCH below to "
            "mutate the assumption at the exact target edge into one concrete development "
            "test likely to expose a genuine skill bug. The mutation must make the selected "
            "edge assumption fail rather than changing the environment to make that assumption "
            "correct, and the visible task must not reveal the recovery path. Reaching the "
            "exact target edge is diagnostic rather than mandatory, but the test must "
            "meaningfully exercise the supplied skill. The mutation must remain achievable "
            "within the unchanged agent budget when the skill gives the right guidance: a "
            f"strong agent must finish implementation and verification in at most "
            f"{config.role_budgets.get('weak_agent', 4)} Pi turns and "
            f"{config.timeouts['pi']} seconds. Do "
            "not merely enlarge the workload, remove an essential capability, require "
            "unavailable credentials or services, or create contradictory requirements. A "
            "relocated or missing tool is valid only when a concrete local recovery path "
            "exists. Explain the bug hypothesis, mutation, and why a SKILL.md patch could "
            "enable success within budget. Make all inline data, expected values, examples, "
            "and prose internally consistent. Keep bug_hypothesis, mutation, and why_patchable "
            "at most 600 characters each. Request one focused artifact or behavior, not a "
            "multi-stage application. Keep the visible prompt at most 2 KiB and the complete "
            "Dockerfile at most 8 KiB. The Dockerfile may install additional packages "
            "online. The root task agent may also install packages online, but all "
            "installation consumes the unchanged task budget. Every capability required by "
            "the prompt must exist when the task starts or be installable within that budget. "
            "The Dockerfile "
            "must not remove, move, or disable software from the base image. If the mutation "
            "depends on a special path or local recovery route, create it explicitly in the "
            "Dockerfile. Use a quoted here-document rather than printf when creating a "
            "multiline helper script so percent signs and shell expressions remain literal. "
            "The Dockerfile may create task inputs, and the prompt must accurately describe "
            "them. Put task and artifact paths under /workspace and do not use /mnt/data or "
            "/tmp in the task prompt. The Dockerfile must be no larger than 8 KiB, start "
            "with exactly "
            f"'FROM {config.docker_image}', contain exactly one FROM, use no ADD or COPY, and "
            "contain exactly 'WORKDIR /workspace'. Preserve the installed Pi runtime. The task "
            "must be compatible with every fixed property, and all property requirements must "
            "be consistent with the visible prompt. Return only one JSON object with exactly "
            "bug_hypothesis, mutation, why_patchable, prompt, and dockerfile; all values must "
            "be nonempty strings. Do not return check prose, check IDs, or any other keys. "
            "The entire response must start with { and end with }. Do not use Markdown fences "
            f"or tools.{correction}\n\n"
            f"BASE IMAGE CAPABILITIES:\n{capability.text}\n\n"
            f"TARGET EDGE ID:\n{target_edge_id}\n\n"
            f"SELECTION REASON:\n{selection_reason}\n\n"
            f"ISOLATED OBSERVED BRANCH:\n{json.dumps(prompt_branch, sort_keys=True)}\n\n"
            f"FIXED PROPERTIES:\n{json.dumps(properties, sort_keys=True)}\n\n"
            f"SKILL.md:\n{skill_text}\n\n"
            "FINAL EXECUTABLE-MUTATION RULE: If the selected bug is an executable "
            "location assumption, install the helper outside the default PATH (for "
            "example under /opt), add no PATH entry or symlink, and ensure the bare "
            "command must fail. The hidden helper must remain locally discoverable.\n"
            "FINAL BLIND-TASK RULE: The visible prompt must not tell the weak agent "
            "to find, locate, discover, search for, or inspect the hidden tool or its "
            "path. It may name the available capability and required artifact only.\n"
            "FINAL RESPONSE RULE: Return exactly one valid JSON object. The first "
            "character must be { and the last character must be }. No Markdown "
            "fences, no trailing comma, no commentary.\n",
            encoding="utf-8",
        )
        mutator_result = pi_runner(
            PiRequest(
                operation_id=f"proposal.skillrace.mutate.{uuid.uuid4().hex}",
                provider=config.provider,
                model=config.model_id,
                prompt_path=mutator_prompt,
                output_dir=mutator_output,
                image=config.docker_image,
                allowed_tools=(),
                max_turns=config.role_budgets["proposer"],
                timeout_seconds=config.timeouts["provider"],
                temperature=1.0,
            )
        )
        if mutator_result.status != "completed":
            raise RuntimeError(f"Pi SkillRACE proposal failed: {mutator_result.status}")
        try:
            response = _selector_json(mutator_result.trace_path)
            if not isinstance(response, dict) or set(response) != response_fields:
                raise ValueError("SkillRACE proposal response is invalid")
            if not all(
                isinstance(response[name], str) and response[name].strip()
                for name in response_fields
            ):
                raise ValueError("SkillRACE proposal fields must be nonempty")
            for field in ("bug_hypothesis", "mutation", "why_patchable"):
                if len(response[field]) > 600:
                    raise ValueError(f"{field} exceeds 600 characters")
            if len(response["prompt"].encode("utf-8")) > 2 * 1024:
                raise ValueError("prompt exceeds 2 KiB")
            if len(response["dockerfile"].encode("utf-8")) > 8 * 1024:
                raise ValueError("dockerfile exceeds 8 KiB")
            mutated_system_paths = set(
                re.findall(
                    r"/(?:usr|opt|bin|sbin|lib|lib64|etc)"
                    r"(?:/[A-Za-z0-9._-]+)+",
                    response["mutation"],
                )
            )
            if any(path in response["prompt"] for path in mutated_system_paths):
                raise ValueError(
                    "visible prompt reveals the mutation's recovery path"
                )
            visible_prompt = response["prompt"].lower()
            reveals_discovery_method = (
                re.search(
                    r"\b(?:find|locate|discover|search for)\b.{0,100}"
                    r"\b(?:executable|binary|tool|utility)\b",
                    visible_prompt,
                    re.DOTALL,
                )
                or re.search(
                    r"\bdo not assume\b.{0,80}\b(?:standard )?path\b",
                    visible_prompt,
                    re.DOTALL,
                )
                or re.search(r"\buse\s+(?:the\s+)?(?:find|which)\b", visible_prompt)
            )
            if reveals_discovery_method:
                raise ValueError(
                    "visible prompt reveals the mutation's recovery method"
                )
            prompt = response["prompt"]
            dockerfile = validate_generated_dockerfile(
                response["dockerfile"], config.docker_image
            )
        except (json.JSONDecodeError, OSError, ValueError) as error:
            mutator_diagnostic = str(error)
            if ordinal < 4:
                continue
            raise ValueError("four invalid SkillRACE proposal responses") from error

        test_id = "skillrace-" + uuid.uuid4().hex
        case = output / test_id
        environment = case / "environment"
        environment.mkdir(parents=True)
        task_path = case / "prompt.txt"
        task_path.write_text(prompt.strip() + "\n", encoding="utf-8")
        checks_path = case / "nl_checks.json"
        atomic_write_json(checks_path, properties)
        (environment / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        atomic_write_json(environment / "sanity.json", {"status": "pass"})
        prompt_hash = file_hash(task_path)
        environment_hash = tree_hash(environment)
        catalog_hash = file_hash(checks_path)
        proposal_receipt = case / "proposal.json"
        atomic_write_json(
            proposal_receipt,
            {
                "schema": "skillrace-generated-test-proposal/1",
                "method": "skillrace",
                "catalog_hash": catalog_hash,
                "prompt_hash": prompt_hash,
                "environment_hash": environment_hash,
                "target_edge_id": target_edge_id,
                "selection_reason": selection_reason,
                "bug_hypothesis": response["bug_hypothesis"].strip(),
                "mutation": response["mutation"].strip(),
                "why_patchable": response["why_patchable"].strip(),
                "selector_input_path": str(selector_input),
                "selector_input_hash": selector_input_hash,
                "selector_pi_receipt_path": str(selector_result.receipt_path),
                "selector_pi_receipt_hash": file_hash(selector_result.receipt_path),
                "pi_receipt_path": str(mutator_result.receipt_path),
                "pi_receipt_hash": file_hash(mutator_result.receipt_path),
                "model": config.model_id,
                "temperature": 1.0,
                "capability_manifest_hash": capability.manifest_hash,
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
            origin_method="skillrace",
            proposal_receipt=proposal_receipt,
            validation_status="pending",
            validation_diagnostic="",
            container_image_id="",
        )
        last = validator(proposed, config)
        if last.validation_status == "valid":
            return last
        mutator_diagnostic = last.validation_diagnostic
    if last is not None:
        return last
    raise RuntimeError("SkillRACE mutator correction loop did not return")
