import json
import hashlib
from pathlib import Path
from typing import Any, Callable
import uuid

from .branch_view import build_edge_index, isolate_branch
from ..pipeline.stages import validate_generated_dockerfile, validate_test
from ..records import ExperimentConfig, RunRecord, SkillVersion, TestCase
from ..runtime.pi import PiRequest, PiResult, run_pi
from ..storage import atomic_write_json, file_hash, tree_hash


PiRunner = Callable[[PiRequest], PiResult]
TestValidator = Callable[[TestCase, ExperimentConfig], TestCase]
_EPISODE_FIELDS = {
    "episode_id",
    "start_event_id",
    "end_event_id",
    "purpose",
    "outcome",
    "reason_for_next",
}


def _trace_events(trace_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("trace events must be JSON objects")
        event_id = event.get("id")
        if isinstance(event_id, str):
            if not event_id or event_id in event_ids:
                raise ValueError("trace event IDs must be nonempty and unique")
            event_ids.add(event_id)
        events.append(event)
    return events


def _is_relevant_event(event: dict[str, Any]) -> bool:
    if event.get("type") != "message" or not isinstance(event.get("id"), str):
        return False
    message = event.get("message")
    if not isinstance(message, dict):
        return False
    if message.get("role") == "toolResult":
        return True
    if message.get("role") != "assistant":
        return False
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(item, dict) and item.get("type") in {"thinking", "toolCall"}
        for item in content
    )


def validate_episodes(
    episodes: Any,
    trace_path: str | Path,
) -> list[dict[str, Any]]:
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("episodes must be a nonempty list")
    events = _trace_events(Path(trace_path))
    positions = {
        event["id"]: index
        for index, event in enumerate(events)
        if isinstance(event.get("id"), str)
    }
    relevant = {
        index for index, event in enumerate(events) if _is_relevant_event(event)
    }
    if not relevant:
        raise ValueError("trace has no relevant reasoning/tool events")
    validated: list[dict[str, Any]] = []
    episode_ids: set[str] = set()
    covered: set[int] = set()
    previous_end = -1
    for episode in episodes:
        if not isinstance(episode, dict) or set(episode) != _EPISODE_FIELDS:
            raise ValueError("episode fields are invalid")
        episode_id = episode["episode_id"]
        if (
            not isinstance(episode_id, str)
            or not episode_id
            or episode_id in episode_ids
        ):
            raise ValueError("episode IDs must be nonempty and unique")
        episode_ids.add(episode_id)
        start_id = episode["start_event_id"]
        end_id = episode["end_event_id"]
        if start_id not in positions or end_id not in positions:
            raise ValueError("episode references an unknown trace event ID")
        start = positions[start_id]
        end = positions[end_id]
        if start > end:
            raise ValueError("episode start must not follow its end")
        if start <= previous_end:
            raise ValueError("episodes overlap or are out of order")
        previous_end = end
        grounded = relevant & set(range(start, end + 1))
        if not grounded:
            raise ValueError("episode range is not grounded in reasoning/tool events")
        covered.update(grounded)
        for field in ("purpose", "outcome"):
            if not isinstance(episode[field], str) or not episode[field].strip():
                raise ValueError(f"episode {field} must be nonempty")
        reason = episode["reason_for_next"]
        if reason is not None and (
            not isinstance(reason, str) or not reason.strip()
        ):
            raise ValueError("episode reason_for_next must be nonempty or null")
        validated.append(
            {
                **episode,
                "purpose": episode["purpose"].strip(),
                "outcome": episode["outcome"].strip(),
                "reason_for_next": reason.strip() if isinstance(reason, str) else None,
            }
        )
    if covered != relevant:
        missing_ids = [events[index]["id"] for index in sorted(relevant - covered)]
        raise ValueError(
            "episode coverage omits relevant reasoning/tool events: "
            + ", ".join(missing_ids)
        )
    return validated


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
        and response.count("```") == 2
    ):
        response = response[len("```json\n") : -len("```")].strip()
    return json.loads(response)


def _selector_json(trace_path: Path) -> Any:
    response = _assistant_text(trace_path)
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        if response.count("```json") != 1 or response.count("```") != 2:
            raise
        start = response.index("```json") + len("```json")
        end = response.index("```", start)
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
    for ordinal in (1, 2):
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
            "finishable in its stated environment within the fixed agent budget. Do not "
            "make a required dependency unavailable without a concrete local recovery path, "
            "require unavailable credentials or services, or use sheer task size as the "
            "challenge. Return only one JSON array of exactly ten objects. Every object "
            "must contain exactly task and environment_conditions, both nonempty strings. "
            "Descriptions are planning inputs, not executable tests. Do not emit property "
            "or check IDs, prompts, Dockerfiles, prose outside the array, or use tools."
            f"{correction}\n\n"
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
                timeout_seconds=config.timeouts["pi"],
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
            descriptions = normalized
            break
        except (json.JSONDecodeError, ValueError) as error:
            diagnostic = str(error)
    if descriptions is None or result is None:
        raise ValueError("two invalid SkillRACE diversity plans")
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
    for replacement in (1, 2):
        attempt = output / f"replacement-{replacement}"
        pi_output = attempt / "pi"
        pi_output.mkdir(parents=True)
        correction = (
            f" Your previous materialization was invalid: {diagnostic}. Generate a fresh "
            "corrected test."
            if diagnostic
            else ""
        )
        prompt_path = pi_output / "prompt.txt"
        prompt_path.write_text(
            "Materialize the selected frozen SkillRACE description into one feasible "
            "development test. Generate its visible task prompt and complete Dockerfile. "
            "The description guides task and environment diversity but does not replace the "
            "complete fixed property catalog. The prompt and Dockerfile must not contradict "
            "the frozen environment conditions. Any required dependency must already exist "
            "in the resulting image or have a concrete local recovery path; the task must "
            "not depend on runtime network access. The task must meaningfully exercise the skill "
            "and remain compatible with every property. Put all task and artifact paths under "
            "/workspace; do not use /mnt/data or /tmp in the task prompt. The Dockerfile must "
            "be no larger than 32 KiB, start with exactly "
            f"'FROM {config.docker_image}', contain exactly one FROM, use no ADD or COPY, "
            "and contain exactly 'WORKDIR /workspace'. Preserve the installed Pi runtime. "
            "Return only one JSON object with exactly prompt and dockerfile, both nonempty "
            f"strings. Do not return checks, IDs, Markdown, or use tools.{correction}\n\n"
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
                timeout_seconds=config.timeouts["pi"],
                temperature=1.0,
            )
        )
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
    raise ValueError("two malformed SkillRACE seed materializations")


def _episode_prompt(
    run_id: str,
    trace_jsonl: str,
    correction: str | None = None,
) -> str:
    suffix = (
        f"\nThe previous response was invalid: {correction}. Return corrected raw JSON."
        if correction
        else ""
    )
    return (
        "Segment the supplied trace's reasoning and tool activity "
        "into ordered, non-overlapping, source-grounded episodes. Cover every assistant "
        "thinking/toolCall event and every toolResult event. Only those events are relevant. "
        "Do not create episodes for text-only assistant messages. Every episode range must "
        "contain at least one relevant event, and the final episode must end at the last "
        "relevant event rather than a later text-only confirmation. Reason internally and "
        "verify the array before answering, but output no reasoning. The top-level JSON type "
        "must be an array. The entire response must begin with [ and end with ]. Do not return "
        "JSONL or NDJSON, multiple root objects, prose, or Markdown fences. "
        "Every item must contain exactly episode_id, start_event_id, end_event_id, "
        "purpose, outcome, and reason_for_next. All IDs must be JSON strings. For example: "
        "{\"episode_id\":\"episode-1\",\"start_event_id\":\"event-id\","
        "\"end_event_id\":\"event-id\",\"purpose\":\"...\",\"outcome\":\"...\","
        "\"reason_for_next\":null}. Event boundaries must use IDs from the "
        "trace. purpose and outcome must be nonempty. reason_for_next is a nonempty "
        f"string or null. Run ID: {run_id}.{suffix}\n\n"
        f"TRACE JSONL:\n{trace_jsonl}"
    )


def create_episodes(
    run: RunRecord,
    config: ExperimentConfig,
    output_dir: str | Path,
    pi_runner: PiRunner = run_pi,
) -> tuple[list[dict[str, Any]], Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    trace_jsonl = run.trace_path.read_text(encoding="utf-8")
    correction: str | None = None
    for ordinal in (1, 2):
        attempt = output / f"episode-attempt-{ordinal}"
        attempt.mkdir()
        prompt_path = attempt / "prompt.txt"
        prompt_path.write_text(
            _episode_prompt(run.run_id, trace_jsonl, correction), encoding="utf-8"
        )
        result = pi_runner(
            PiRequest(
                operation_id=f"episodes.{run.run_id}.{uuid.uuid4().hex}",
                provider=config.provider,
                model=config.model_id,
                prompt_path=prompt_path,
                output_dir=attempt,
                image=config.docker_image,
                allowed_tools=("read",),
                max_turns=config.role_budgets["segmenter"],
                timeout_seconds=config.timeouts["pi"],
                mounts=((run.trace_path, "/input/run-trace.jsonl", "ro"),),
            )
        )
        if result.status != "completed":
            raise RuntimeError(f"Pi episode creation failed: {result.status}")
        try:
            parsed = _assistant_json(result.trace_path)
            episodes = validate_episodes(parsed, run.trace_path)
        except (json.JSONDecodeError, ValueError) as error:
            correction = str(error)
            if ordinal == 1:
                continue
            raise ValueError("two invalid episode responses") from error
        atomic_write_json(output / "episodes.json", episodes)
        atomic_write_json(
            output / "episode-creation.json",
            {
                "schema": "skillrace-episode-creation/1",
                "run_id": run.run_id,
                "model": config.model_id,
                "episode_count": len(episodes),
                "pi_receipt_path": str(result.receipt_path),
            },
        )
        return episodes, result.receipt_path
    raise RuntimeError("episode creation loop did not return")


_NODE_FIELDS = {
    "node_id",
    "purpose",
    "outcome",
    "member_run_ids",
    "member_episode_ids",
    "reach_status",
    "failure_ids",
}
_EDGE_FIELDS = {"source_node_id", "target_node_id", "reason"}


def validate_tree(tree: Any) -> dict[str, Any]:
    if not isinstance(tree, dict) or set(tree) != {"schema", "nodes", "edges"}:
        raise ValueError("tree fields are invalid")
    if tree["schema"] != "skillrace-reasoning-tree/1":
        raise ValueError("tree schema is invalid")
    if not isinstance(tree["nodes"], list) or not isinstance(tree["edges"], list):
        raise ValueError("tree nodes and edges must be lists")
    node_ids: set[str] = set()
    memberships: set[tuple[str, str]] = set()
    for node in tree["nodes"]:
        if not isinstance(node, dict) or set(node) != _NODE_FIELDS:
            raise ValueError("tree node fields are invalid")
        node_id = node["node_id"]
        if not isinstance(node_id, str) or not node_id or node_id in node_ids:
            raise ValueError("tree node IDs must be nonempty and unique")
        node_ids.add(node_id)
        if not all(
            isinstance(node[name], str) and node[name].strip()
            for name in ("purpose", "outcome")
        ):
            raise ValueError("tree node purpose and outcome must be nonempty")
        runs = node["member_run_ids"]
        episodes = node["member_episode_ids"]
        failures = node["failure_ids"]
        if (
            not isinstance(runs, list)
            or not isinstance(episodes, list)
            or len(runs) != len(episodes)
            or not all(isinstance(item, str) and item for item in runs + episodes)
            or not isinstance(failures, list)
            or not all(isinstance(item, str) and item for item in failures)
        ):
            raise ValueError("tree membership or failure IDs are invalid")
        for membership in zip(runs, episodes, strict=True):
            if membership in memberships:
                raise ValueError("duplicate membership in reasoning tree")
            memberships.add(membership)
        if node["reach_status"] not in {
            "reached",
            "unreached",
            "reasoning_unexplored",
        }:
            raise ValueError("tree reach status is invalid")
    if "root" not in node_ids:
        raise ValueError("tree must contain root")
    edge_pairs: set[tuple[str, str]] = set()
    for edge in tree["edges"]:
        if not isinstance(edge, dict) or set(edge) != _EDGE_FIELDS:
            raise ValueError("tree edge fields are invalid")
        pair = (edge["source_node_id"], edge["target_node_id"])
        if (
            pair[0] not in node_ids
            or pair[1] not in node_ids
            or pair in edge_pairs
            or not isinstance(edge["reason"], str)
            or not edge["reason"].strip()
        ):
            raise ValueError("tree edge is invalid")
        edge_pairs.add(pair)
    return json.loads(json.dumps(tree))


def _alignment_parent(
    tree: dict[str, Any],
    episodes: list[dict[str, Any]],
    run_id: str,
    config: ExperimentConfig,
    output: Path,
    pi_runner: PiRunner,
) -> tuple[str, Path]:
    diagnostic: str | None = None
    known_nodes = {node["node_id"] for node in tree["nodes"]}
    for ordinal in (1, 2):
        attempt = output / f"alignment-attempt-{ordinal}"
        attempt.mkdir(parents=True)
        prompt_path = attempt / "prompt.txt"
        correction = (
            f"\nYour previous response was invalid: {diagnostic}. Return corrected raw "
            "JSON only."
            if diagnostic
            else ""
        )
        prompt_path.write_text(
            "Choose the single existing parent node under which this ordered run episode "
            "chain best belongs. Return only one JSON object with exactly parent_node_id. "
            "The entire response must start with { and end with }. Do not use Markdown "
            "fences. The value must be an existing node ID. This is one batched alignment "
            "decision for the complete episode list."
            f"{correction}\n\n"
            f"TREE:\n{json.dumps(tree, sort_keys=True)}\n\n"
            f"EPISODES:\n{json.dumps(episodes, sort_keys=True)}\n",
            encoding="utf-8",
        )
        result = pi_runner(
            PiRequest(
                operation_id=f"tree-alignment.{run_id}.{uuid.uuid4().hex}",
                provider=config.provider,
                model=config.model_id,
                prompt_path=prompt_path,
                output_dir=attempt,
                image=config.docker_image,
                allowed_tools=("read",),
                max_turns=config.role_budgets["tree_alignment"],
                timeout_seconds=config.timeouts["pi"],
            )
        )
        if result.status != "completed":
            raise RuntimeError(f"Pi tree alignment failed: {result.status}")
        try:
            response = _assistant_json(result.trace_path)
            if not isinstance(response, dict) or set(response) != {"parent_node_id"}:
                raise ValueError("tree alignment response is invalid")
            parent = response["parent_node_id"]
            if parent not in known_nodes:
                raise ValueError("tree alignment selected an unknown parent")
        except (json.JSONDecodeError, ValueError) as error:
            diagnostic = str(error)
            if ordinal == 1:
                continue
            raise ValueError("two invalid tree alignment responses") from error
        return parent, result.receipt_path
    raise RuntimeError("tree alignment loop did not return")


def _new_node_id(run_id: str, episode_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}:{episode_id}".encode("utf-8")).hexdigest()
    return "node-" + digest[:16]


def merge_episodes(
    tree: dict[str, Any],
    episodes: list[dict[str, Any]],
    run_id: str,
    failures: list[dict[str, str]],
    config: ExperimentConfig,
    output_dir: str | Path,
    pi_runner: PiRunner = run_pi,
) -> dict[str, Any]:
    merged = validate_tree(tree)
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be nonempty")
    episode_ids = {episode["episode_id"] for episode in episodes}
    for node in merged["nodes"]:
        if any(
            existing_run == run_id and existing_episode in episode_ids
            for existing_run, existing_episode in zip(
                node["member_run_ids"], node["member_episode_ids"], strict=True
            )
        ):
            raise ValueError("duplicate membership in reasoning tree")
    failures_by_episode: dict[str, list[str]] = {item: [] for item in episode_ids}
    for failure in failures:
        if (
            not isinstance(failure, dict)
            or set(failure) != {"failure_id", "episode_id"}
            or failure["episode_id"] not in episode_ids
            or not isinstance(failure["failure_id"], str)
            or not failure["failure_id"]
        ):
            raise ValueError("failure link is invalid")
        failures_by_episode[failure["episode_id"]].append(failure["failure_id"])
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    first = episodes[0]
    exact_first = next(
        (
            node
            for node in merged["nodes"]
            if node["purpose"] == first["purpose"]
            and node["outcome"] == first["outcome"]
        ),
        None,
    )
    alignment_receipt: Path | None = None
    if exact_first is not None:
        parent_id = exact_first["node_id"]
    elif len(merged["nodes"]) == 1:
        parent_id = "root"
    else:
        parent_id, alignment_receipt = _alignment_parent(
            merged, episodes, run_id, config, output, pi_runner
        )
    previous_id = parent_id
    previous_reason: str | None = None
    for episode in episodes:
        node = next(
            (
                item
                for item in merged["nodes"]
                if item["purpose"] == episode["purpose"]
                and item["outcome"] == episode["outcome"]
            ),
            None,
        )
        if node is None:
            node = {
                "node_id": _new_node_id(run_id, episode["episode_id"]),
                "purpose": episode["purpose"],
                "outcome": episode["outcome"],
                "member_run_ids": [],
                "member_episode_ids": [],
                "reach_status": "reached",
                "failure_ids": [],
            }
            merged["nodes"].append(node)
        node["member_run_ids"].append(run_id)
        node["member_episode_ids"].append(episode["episode_id"])
        node["reach_status"] = "reached"
        for failure_id in failures_by_episode[episode["episode_id"]]:
            if failure_id not in node["failure_ids"]:
                node["failure_ids"].append(failure_id)
        if previous_id != node["node_id"] and not any(
            edge["source_node_id"] == previous_id
            and edge["target_node_id"] == node["node_id"]
            for edge in merged["edges"]
        ):
            merged["edges"].append(
                {
                    "source_node_id": previous_id,
                    "target_node_id": node["node_id"],
                    "reason": previous_reason or episode["purpose"],
                }
            )
        previous_id = node["node_id"]
        previous_reason = episode["reason_for_next"] or episode["purpose"]
    validated = validate_tree(merged)
    atomic_write_json(output / "tree.json", validated)
    atomic_write_json(
        output / "tree-merge.json",
        {
            "schema": "skillrace-tree-merge/1",
            "run_id": run_id,
            "alignment_receipt_path": (
                str(alignment_receipt) if alignment_receipt is not None else None
            ),
            "node_count": len(validated["nodes"]),
            "edge_count": len(validated["edges"]),
        },
    )
    return validated


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
    selector_output = output / "selector-pi"
    selector_output.mkdir()
    selector_prompt = selector_output / "prompt.txt"
    selector_prompt.write_text(
        "Act as the SkillRACE edge selector. Choose exactly one real observed reasoning "
        "edge from the COMPACT EDGE INDEX below. Prefer the edge whose assumption has the "
        "best chance of exposing a genuine, patchable skill failure under the fixed checks. "
        "The resulting task must remain achievable within the unchanged agent budget when "
        "the skill gives the right guidance; do not select sheer difficulty, impossible "
        "requirements, unavailable credentials, or an environment condition without a "
        "concrete local recovery route. Return only one JSON object with exactly "
        "target_edge_id and selection_reason, both nonempty strings. The edge ID must be "
        "copied exactly from the index. Do not use tools.\n\n"
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
            timeout_seconds=config.timeouts["pi"],
            temperature=1.0,
        )
    )
    if selector_result.status != "completed":
        raise RuntimeError(
            f"Pi SkillRACE edge selection failed: {selector_result.status}"
        )
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

    selected_branch = isolate_branch(validated_tree, target_edge_id)
    atomic_write_json(selector_input / "selected-branch.json", selected_branch)
    selector_input_hash = tree_hash(selector_input)

    mutator_output = output / "mutator-pi"
    mutator_output.mkdir()
    mutator_prompt = mutator_output / "prompt.txt"
    mutator_prompt.write_text(
        "Act as the SkillRACE test mutator. Use the ISOLATED OBSERVED BRANCH below to "
        "mutate the assumption at the exact target edge into one concrete development "
        "test likely to expose a genuine skill bug. The mutation must make the selected "
        "edge assumption fail rather than changing the environment to make that assumption "
        "correct, and the visible task must not reveal the recovery path. Reaching the exact target edge is "
        "diagnostic rather than mandatory, but the test must meaningfully exercise the "
        "supplied skill. The mutation must remain achievable within the unchanged agent "
        "budget when the skill gives the right guidance. Do not merely enlarge the workload, "
        "remove an essential capability, require unavailable credentials or services, or "
        "create contradictory requirements. A relocated or missing tool is valid only when "
        "a concrete local recovery path exists. Explain the bug hypothesis, mutation, and "
        "why a SKILL.md patch could enable success within budget. Make all inline data, "
        "expected values, examples, and prose internally consistent. Generate a self-contained "
        "visible prompt and complete Dockerfile. The Dockerfile must not download or install "
        "packages or contact external services; use only software already present in the base "
        "image and create environment variations with local files or symlinks. Every "
        "capability required by the prompt must exist when the task container starts. The "
        "Dockerfile must not remove, move, or disable software from the base image. If the "
        "mutation depends on a special path or local recovery route, create it explicitly in "
        "the Dockerfile. Use a quoted here-document rather than printf when creating a "
        "multiline helper script so percent signs and shell expressions remain literal. The "
        "Dockerfile may create task inputs, and the prompt must accurately describe them. "
        "Put task and artifact paths under /workspace and do not use /mnt/data or /tmp in "
        "the task prompt. The Dockerfile must be no larger than 32 KiB, start with exactly "
        f"'FROM {config.docker_image}', contain exactly one FROM, use no ADD or COPY, and "
        "contain exactly 'WORKDIR /workspace'. Preserve the installed Pi runtime. The task "
        "must be compatible with every fixed property, and all property requirements must "
        "be consistent with the visible prompt. Return only one JSON object with exactly "
        "bug_hypothesis, mutation, why_patchable, prompt, and dockerfile; all values must "
        "be nonempty strings. "
        "Do not return check prose, check IDs, or any other keys. The entire response must "
        "start with { and end with }. Do not use Markdown fences or tools.\n\n"
        f"TARGET EDGE ID:\n{target_edge_id}\n\n"
        f"SELECTION REASON:\n{selection_reason}\n\n"
        f"ISOLATED OBSERVED BRANCH:\n{json.dumps(selected_branch, sort_keys=True)}\n\n"
        f"FIXED PROPERTIES:\n{json.dumps(properties, sort_keys=True)}\n\n"
        f"SKILL.md:\n{skill_text}\n",
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
            timeout_seconds=config.timeouts["pi"],
            temperature=1.0,
        )
    )
    if mutator_result.status != "completed":
        raise RuntimeError(f"Pi SkillRACE proposal failed: {mutator_result.status}")
    response = _selector_json(mutator_result.trace_path)
    response_fields = {
        "bug_hypothesis",
        "mutation",
        "why_patchable",
        "prompt",
        "dockerfile",
    }
    if not isinstance(response, dict) or set(response) != response_fields:
        raise ValueError("SkillRACE proposal response is invalid")
    if not all(isinstance(response[name], str) and response[name].strip() for name in response_fields):
        raise ValueError("SkillRACE proposal fields must be nonempty")
    prompt = response["prompt"]
    dockerfile = validate_generated_dockerfile(
        response["dockerfile"], config.docker_image
    )
    test_id = "skillrace-" + uuid.uuid4().hex
    case = output / test_id
    environment = case / "environment"
    environment.mkdir(parents=True)
    task_path = case / "prompt.txt"
    task_path.write_text(prompt.strip() + "\n", encoding="utf-8")
    checks_path = case / "nl_checks.json"
    atomic_write_json(checks_path, properties)
    (environment / "Dockerfile").write_text(
        dockerfile, encoding="utf-8"
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
    return validator(proposed, config)
