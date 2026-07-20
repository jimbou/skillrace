import json
import hashlib
from pathlib import Path
from typing import Any, Callable
import uuid

from ..pipeline.stages import validate_test
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


def _assistant_json(trace_path: Path) -> Any:
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
    response = responses[-1].strip()
    if (
        response.startswith("```json\n")
        and response.endswith("\n```")
        and response.count("```") == 2
    ):
        response = response[len("```json\n") : -len("\n```")]
    return json.loads(response)


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
    attempt = output / "alignment"
    attempt.mkdir(parents=True)
    prompt_path = attempt / "prompt.txt"
    prompt_path.write_text(
        "Choose the single existing parent node under which this ordered run episode "
        "chain best belongs. Return only one JSON object with exactly parent_node_id. "
        "The entire response must start with { and end with }. Do not use Markdown fences. "
        "The value must be an existing node ID. This is one batched alignment decision "
        "for the complete episode list.\n\n"
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
    response = _assistant_json(result.trace_path)
    if not isinstance(response, dict) or set(response) != {"parent_node_id"}:
        raise ValueError("tree alignment response is invalid")
    parent = response["parent_node_id"]
    if parent not in {node["node_id"] for node in tree["nodes"]}:
        raise ValueError("tree alignment selected an unknown parent")
    return parent, result.receipt_path


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


def select_unreached_branch(tree: dict[str, Any]) -> dict[str, Any] | None:
    validated = validate_tree(tree)
    unexplored = sorted(
        (
            node
            for node in validated["nodes"]
            if node["node_id"] != "root"
            and node["reach_status"] in {"unreached", "reasoning_unexplored"}
        ),
        key=lambda node: node["node_id"],
    )
    if unexplored:
        return unexplored[0]
    failed = sorted(
        (
            node
            for node in validated["nodes"]
            if node["node_id"] != "root" and node["failure_ids"]
        ),
        key=lambda node: node["node_id"],
    )
    return failed[0] if failed else None


def propose_test(
    tree: dict[str, Any],
    skill: SkillVersion,
    config: ExperimentConfig,
    *,
    pi_runner: PiRunner = run_pi,
    validator: TestValidator = validate_test,
) -> TestCase:
    target = select_unreached_branch(tree)
    if target is None:
        raise ValueError("reasoning tree has no unreached branch")
    output = config.output_root / "skillrace-proposals" / uuid.uuid4().hex
    attempt = output / "pi"
    attempt.mkdir(parents=True)
    skill_text = (skill.directory_path / "SKILL.md").read_text(encoding="utf-8")
    prompt_path = attempt / "prompt.txt"
    prompt_path.write_text(
        "Propose one concrete development task that exercises the selected unreached "
        "reasoning branch. The task must be self-contained: specify all paths, inline "
        "input data, and an observable expected result. The task container starts with an "
        "empty /workspace, so do not claim that a file or project already exists. Tell the "
        "agent to create every needed file, and put every task and artifact path under "
        "/workspace. Do not use /mnt/data or /tmp. The check_description must not add "
        "requirements absent from the visible task prompt. Return only one JSON object with "
        "exactly prompt and check_description; both values must be nonempty strings. "
        "check_description must state a concrete artifact observation. The entire response "
        "must start with { and end with }. "
        "Do not use Markdown fences or tools.\n\n"
        f"SELECTED BRANCH:\n{json.dumps(target, sort_keys=True)}\n\n"
        f"SKILL.md:\n{skill_text}\n",
        encoding="utf-8",
    )
    result = pi_runner(
        PiRequest(
            operation_id=f"proposal.skillrace.{target['node_id']}.{uuid.uuid4().hex}",
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
        raise RuntimeError(f"Pi SkillRACE proposal failed: {result.status}")
    response = _assistant_json(result.trace_path)
    if not isinstance(response, dict) or set(response) != {
        "prompt",
        "check_description",
    }:
        raise ValueError("SkillRACE proposal response is invalid")
    prompt = response["prompt"]
    check_description = response["check_description"]
    if (
        not isinstance(prompt, str)
        or not prompt.strip()
        or not isinstance(check_description, str)
        or not check_description.strip()
    ):
        raise ValueError("SkillRACE proposal fields must be nonempty")
    test_id = "skillrace-" + uuid.uuid4().hex
    case = output / test_id
    environment = case / "environment"
    environment.mkdir(parents=True)
    task_path = case / "prompt.txt"
    task_path.write_text(prompt.strip() + "\n", encoding="utf-8")
    checks_path = case / "nl_checks.json"
    atomic_write_json(
        checks_path,
        [
            {
                "property_id": "P1",
                "description": (
                    f"{check_description.strip()} Target branch {target['node_id']}: "
                    f"{target['purpose']}."
                ),
            }
        ],
    )
    if "\n" in config.docker_image:
        raise ValueError("docker image must not contain a newline")
    (environment / "Dockerfile").write_text(
        f"FROM {config.docker_image}\nWORKDIR /workspace\n", encoding="utf-8"
    )
    atomic_write_json(environment / "sanity.json", {"status": "pass"})
    proposal_receipt = case / "proposal.json"
    atomic_write_json(
        proposal_receipt,
        {
            "schema": "skillrace-branch-proposal/1",
            "target_node_id": target["node_id"],
            "target_reach_status": target["reach_status"],
            "pi_receipt_path": str(result.receipt_path),
            "model": config.model_id,
        },
    )
    proposed = TestCase(
        test_id=test_id,
        prompt_path=task_path,
        prompt_hash=file_hash(task_path),
        environment_directory=environment,
        environment_hash=tree_hash(environment),
        nl_check_path=checks_path,
        nl_check_hash=file_hash(checks_path),
        origin_method="skillrace",
        proposal_receipt=proposal_receipt,
        validation_status="pending",
        validation_diagnostic="",
        container_image_id="",
    )
    return validator(proposed, config)
