"""Contextual observed-behavior tree records and direct episode-line folding."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Callable
import uuid

from ..records import ExperimentConfig
from ..runtime.pi import PiRequest, PiResult, run_pi
from ..storage import atomic_write_json, canonical_json_hash


PiRunner = Callable[[PiRequest], PiResult]
_TOP_FIELDS = {
    "schema",
    "runs",
    "next_id",
    "root_children",
    "root_edges",
    "nodes",
}
_NODE_FIELDS = {
    "id",
    "purpose",
    "what_it_did_variants",
    "runs",
    "members",
    "children",
    "edges",
    "reach_status",
    "failure_ids",
}
_MEMBER_FIELDS = {
    "run_id",
    "episode_id",
    "purpose",
    "what_it_did",
    "outcome",
    "opening_reasoning",
}
_VARIANT_FIELDS = {"text", "run_ids"}
_TRANSITION_FIELDS = {"run_id", "in_outcome", "reasoning"}
_EPISODE_FIELDS = {
    "episode_id",
    "start_call",
    "end_call",
    "purpose",
    "what_it_did",
    "outcome",
    "opening_reasoning",
}
_REACH_STATUSES = {"reached", "unreached", "reasoning_unexplored"}
JUDGMENT_INSTRUCTIONS = {
    "same-purpose": (
        "Decide whether the two coding-agent episodes pursue the same concrete component "
        "and technical objective. Purpose is primary and actions clarify its scope. "
        "Different features, bugs, repairs, or validation targets are not the same. A "
        "narrow subset is not the same as an episode containing materially additional "
        "work. Return false when the only common label would be a generic lifecycle "
        "phase, language, skill, tool sequence, or workflow such as implementing, "
        "debugging, testing, or creating and verifying a file. Outcomes are deliberately "
        "absent and must not affect the decision. Return only one raw JSON object with "
        "exactly same (boolean) and reason (nonempty string). Do not return prose or "
        "Markdown fences."
    ),
    "broaden-purpose": (
        "Two episodes were provisionally judged to have the same purpose. Admit the "
        "merge only if one concise purpose remains concrete and truthfully describes "
        "both without adding work absent from either. It may remove incidental wording "
        "or filenames but must retain the shared component, behavior, bug, repair, or "
        "validation target. Reject generic labels such as implementing functionality, "
        "debugging, running tests, or creating and verifying a file. Return only one raw "
        "JSON object with exactly mergeable (boolean), purpose (a nonempty string when "
        "mergeable is true, otherwise null), and reason (nonempty string). Do not return "
        "prose or Markdown fences."
    ),
    "same-approach": (
        "Decide whether two episodes with the same concrete purpose used essentially "
        "the same actual technical method. Ignore only incidental wording. Sharing a "
        "language, skill, tool, test workflow, or generic action sequence is insufficient; "
        "different algorithms, fixes, or investigation strategies are different "
        "approaches. Return only one raw JSON object with exactly same (boolean). Do not "
        "return prose or Markdown fences."
    ),
}


def empty_tree() -> dict[str, Any]:
    return {
        "schema": "behavior-tree/2",
        "runs": {},
        "next_id": 0,
        "root_children": [],
        "root_edges": {},
        "nodes": {},
    }


def _nonempty_strings(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and item.strip() for item in value)
        and len(value) == len(set(value))
    )


def _validate_transition_list(
    transitions: Any,
    child_runs: set[str],
    known_runs: set[str],
    *,
    root: bool,
    parent_runs: set[str] | None = None,
) -> None:
    if not isinstance(transitions, list) or not transitions:
        raise ValueError("tree transition list is invalid")
    transition_runs: set[str] = set()
    for transition in transitions:
        if not isinstance(transition, dict) or set(transition) != _TRANSITION_FIELDS:
            raise ValueError("tree transition fields are invalid")
        run_id = transition["run_id"]
        if (
            not isinstance(run_id, str)
            or not run_id
            or run_id in transition_runs
            or run_id not in known_runs
            or run_id not in child_runs
            or (parent_runs is not None and run_id not in parent_runs)
        ):
            raise ValueError("tree transition run membership is invalid")
        transition_runs.add(run_id)
        outcome = transition["in_outcome"]
        if root:
            if outcome is not None:
                raise ValueError("root transition outcome must be null")
        elif not isinstance(outcome, str) or not outcome.strip():
            raise ValueError("internal transition outcome must be nonempty")
        reasoning = transition["reasoning"]
        if not isinstance(reasoning, str) or not reasoning.strip():
            raise ValueError("tree transition reasoning must be nonempty")
    if transition_runs != child_runs:
        raise ValueError("tree transition runs do not match child memberships")


def validate_tree(tree: Any) -> dict[str, Any]:
    """Validate the complete rooted tree, memberships, variants, and transitions."""
    if not isinstance(tree, dict) or set(tree) != _TOP_FIELDS:
        raise ValueError("tree fields are invalid")
    if tree["schema"] != "behavior-tree/2":
        raise ValueError("tree schema is invalid")
    runs = tree["runs"]
    if not isinstance(runs, dict) or any(
        not isinstance(run_id, str)
        or not run_id
        or not isinstance(meta, dict)
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            for key, value in meta.items()
        )
        for run_id, meta in runs.items()
    ):
        raise ValueError("tree run registry is invalid")
    known_runs = set(runs)
    next_id = tree["next_id"]
    if isinstance(next_id, bool) or not isinstance(next_id, int) or next_id < 0:
        raise ValueError("tree next_id is invalid")
    nodes = tree["nodes"]
    if not isinstance(nodes, dict):
        raise ValueError("tree nodes must be an object")
    root_children = tree["root_children"]
    root_edges = tree["root_edges"]
    if (
        not isinstance(root_children, list)
        or len(root_children) != len(set(root_children))
        or not all(isinstance(item, str) and item for item in root_children)
        or not isinstance(root_edges, dict)
        or set(root_edges) != set(root_children)
    ):
        raise ValueError("tree root children or edges are invalid")

    memberships: set[tuple[str, str]] = set()
    numeric_ids: list[int] = []
    for node_id, node in nodes.items():
        match = re.fullmatch(r"n(\d+)", node_id) if isinstance(node_id, str) else None
        if match is None or not isinstance(node, dict) or set(node) != _NODE_FIELDS:
            raise ValueError("tree node fields or ID are invalid")
        numeric_ids.append(int(match.group(1)))
        if node["id"] != node_id:
            raise ValueError("tree node key and ID differ")
        if not isinstance(node["purpose"], str) or not node["purpose"].strip():
            raise ValueError("tree node purpose must be nonempty")
        node_runs = node["runs"]
        if not _nonempty_strings(node_runs) or not set(node_runs) <= known_runs:
            raise ValueError("tree node runs are invalid")
        members = node["members"]
        if not isinstance(members, list) or not members:
            raise ValueError("tree node members are invalid")
        member_runs: set[str] = set()
        for member in members:
            if not isinstance(member, dict) or set(member) != _MEMBER_FIELDS:
                raise ValueError("tree member fields are invalid")
            if any(
                not isinstance(member[field], str) or not member[field].strip()
                for field in _MEMBER_FIELDS
            ):
                raise ValueError("tree member values must be nonempty")
            membership = (member["run_id"], member["episode_id"])
            if membership in memberships:
                raise ValueError("duplicate membership in behavior tree")
            memberships.add(membership)
            member_runs.add(member["run_id"])
        if member_runs != set(node_runs):
            raise ValueError("tree node runs do not match member runs")

        variants = node["what_it_did_variants"]
        if not isinstance(variants, list) or not variants:
            raise ValueError("tree approach variant list is invalid")
        variant_runs: set[str] = set()
        variant_texts: set[str] = set()
        for variant in variants:
            if not isinstance(variant, dict) or set(variant) != _VARIANT_FIELDS:
                raise ValueError("tree approach variant fields are invalid")
            text = variant["text"]
            run_ids = variant["run_ids"]
            if (
                not isinstance(text, str)
                or not text.strip()
                or text in variant_texts
                or not _nonempty_strings(run_ids)
                or not set(run_ids) <= set(node_runs)
            ):
                raise ValueError("tree approach variant is invalid")
            variant_texts.add(text)
            variant_runs.update(run_ids)
        if variant_runs != set(node_runs):
            raise ValueError("tree approach variants do not cover node runs")
        children = node["children"]
        edges = node["edges"]
        if (
            not isinstance(children, list)
            or len(children) != len(set(children))
            or not all(isinstance(item, str) and item for item in children)
            or not isinstance(edges, dict)
            or set(edges) != set(children)
        ):
            raise ValueError("tree node children or edges are invalid")
        if node["reach_status"] not in _REACH_STATUSES:
            raise ValueError("tree node reach status is invalid")
        if not _nonempty_strings(node["failure_ids"]):
            raise ValueError("tree node failure IDs are invalid")

    if numeric_ids and next_id <= max(numeric_ids):
        raise ValueError("tree next_id does not follow allocated node IDs")
    known_nodes = set(nodes)
    all_children = list(root_children)
    for node in nodes.values():
        all_children.extend(node["children"])
    unknown = set(all_children) - known_nodes
    if unknown:
        raise ValueError("tree references an unknown child")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError("tree contains a cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        for child_id in nodes[node_id]["children"]:
            visit(child_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in root_children:
        visit(node_id)
    if visited != known_nodes:
        raise ValueError("tree contains an unreachable node")

    parent_counts = {node_id: 0 for node_id in known_nodes}
    for child_id in root_children:
        parent_counts[child_id] += 1
    for node in nodes.values():
        for child_id in node["children"]:
            parent_counts[child_id] += 1
    if any(count != 1 for count in parent_counts.values()):
        raise ValueError("tree node must have exactly one parent")

    for child_id in root_children:
        _validate_transition_list(
            root_edges[child_id],
            set(nodes[child_id]["runs"]),
            known_runs,
            root=True,
        )
    for node in nodes.values():
        for child_id in node["children"]:
            _validate_transition_list(
                node["edges"][child_id],
                set(nodes[child_id]["runs"]),
                known_runs,
                root=False,
                parent_runs=set(node["runs"]),
            )
    return json.loads(json.dumps(tree))


def _validate_input_episodes(episodes: Any) -> list[dict[str, Any]]:
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("episodes must be a nonempty list")
    validated: list[dict[str, Any]] = []
    episode_ids: set[str] = set()
    for episode in episodes:
        if not isinstance(episode, dict) or set(episode) != _EPISODE_FIELDS:
            raise ValueError("episode fields are invalid")
        if (
            isinstance(episode["start_call"], bool)
            or isinstance(episode["end_call"], bool)
            or not isinstance(episode["start_call"], int)
            or not isinstance(episode["end_call"], int)
        ):
            raise ValueError("episode spans must be integers")
        if any(
            not isinstance(episode[field], str) or not episode[field].strip()
            for field in (
                "episode_id",
                "purpose",
                "what_it_did",
                "outcome",
                "opening_reasoning",
            )
        ):
            raise ValueError("episode text fields must be nonempty")
        if episode["episode_id"] in episode_ids:
            raise ValueError("episode IDs must be unique")
        episode_ids.add(episode["episode_id"])
        validated.append(json.loads(json.dumps(episode)))
    return validated


def _new_node(
    tree: dict[str, Any], episode: dict[str, Any], run_id: str
) -> str:
    node_id = f"n{tree['next_id']}"
    tree["next_id"] += 1
    tree["nodes"][node_id] = {
        "id": node_id,
        "purpose": episode["purpose"],
        "what_it_did_variants": [
            {"text": episode["what_it_did"], "run_ids": [run_id]}
        ],
        "runs": [run_id],
        "members": [],
        "children": [],
        "edges": {},
        "reach_status": "reached",
        "failure_ids": [],
    }
    return node_id


def _add_member(
    tree: dict[str, Any], node_id: str, episode: dict[str, Any], run_id: str
) -> None:
    node = tree["nodes"][node_id]
    node["members"].append(
        {
            "run_id": run_id,
            "episode_id": episode["episode_id"],
            "purpose": episode["purpose"],
            "what_it_did": episode["what_it_did"],
            "outcome": episode["outcome"],
            "opening_reasoning": episode["opening_reasoning"],
        }
    )
    if run_id not in node["runs"]:
        node["runs"].append(run_id)


def _link(
    tree: dict[str, Any],
    parent_id: str | None,
    child_id: str,
    run_id: str,
    previous_outcome: str | None,
    reasoning: str,
) -> None:
    transition = {
        "run_id": run_id,
        "in_outcome": previous_outcome,
        "reasoning": reasoning,
    }
    if parent_id is None:
        if child_id not in tree["root_children"]:
            tree["root_children"].append(child_id)
        tree["root_edges"].setdefault(child_id, []).append(transition)
        return
    parent = tree["nodes"][parent_id]
    if child_id not in parent["children"]:
        parent["children"].append(child_id)
    parent["edges"].setdefault(child_id, []).append(transition)


def _assistant_json(trace_path: Path) -> Any:
    responses: list[str] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        if text:
            responses.append(text.strip())
    if not responses:
        raise ValueError("tree judgment contains no assistant JSON")
    try:
        return json.loads(responses[-1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"assistant response is not valid JSON: {exc.msg}") from exc


def _validate_judgment(
    kind: str, value: Any, expected_fields: set[str]
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise ValueError(f"{kind} response fields are invalid")
    if kind in {"same-purpose", "same-approach"} and not isinstance(
        value.get("same"), bool
    ):
        raise ValueError(f"{kind} same must be a boolean")
    if kind == "same-purpose" and (
        not isinstance(value.get("reason"), str) or not value["reason"].strip()
    ):
        raise ValueError("same-purpose reason must be nonempty")
    if kind == "broaden-purpose":
        mergeable = value.get("mergeable")
        purpose = value.get("purpose")
        reason = value.get("reason")
        if not isinstance(mergeable, bool):
            raise ValueError("broaden-purpose mergeable must be a boolean")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("broaden-purpose reason must be nonempty")
        if mergeable and (not isinstance(purpose, str) or not purpose.strip()):
            raise ValueError("broaden-purpose admitted purpose must be nonempty")
        if not mergeable and purpose is not None:
            raise ValueError("broaden-purpose rejected purpose must be null")
    return value


def _cached_judgment(
    kind: str,
    payload: dict[str, Any],
    expected_fields: set[str],
    cache: dict[str, Any],
    config: ExperimentConfig,
    output: Path,
    pi_runner: PiRunner,
) -> dict[str, Any]:
    key = kind + ":" + canonical_json_hash(payload)
    if key in cache:
        return _validate_judgment(kind, cache[key], expected_fields)
    diagnostic: str | None = None
    root = output / "judgments" / kind / key.split(":", 1)[1]
    root.mkdir(parents=True, exist_ok=True)
    for ordinal in (1, 2, 3):
        attempt = root / f"attempt-{ordinal}"
        attempt.mkdir()
        prompt_path = attempt / "prompt.txt"
        correction = (
            f"\nPrevious response invalid: {diagnostic}. Return corrected raw JSON."
            if diagnostic
            else ""
        )
        prompt_path.write_text(
            JUDGMENT_INSTRUCTIONS[kind]
            + correction
            + "\n\nINPUT:\n"
            + json.dumps(payload, sort_keys=True),
            encoding="utf-8",
        )
        result = pi_runner(
            PiRequest(
                operation_id=f"tree.{kind}.{uuid.uuid4().hex}",
                provider=config.provider,
                model=config.model_id,
                prompt_path=prompt_path,
                output_dir=attempt,
                image=config.docker_image,
                allowed_tools=(),
                max_turns=config.role_budgets["tree_alignment"],
                timeout_seconds=config.timeouts["provider"],
                temperature=0,
            )
        )
        if result.status != "completed":
            raise RuntimeError(f"Pi tree judgment failed: {result.status}")
        try:
            parsed = _validate_judgment(
                kind, _assistant_json(result.trace_path), expected_fields
            )
        except ValueError as error:
            diagnostic = str(error)
            if ordinal < 3:
                continue
            raise ValueError(f"three invalid {kind} responses") from error
        cache[key] = parsed
        atomic_write_json(
            root / "judgment.json",
            {
                "kind": kind,
                "cache_key": key,
                "result": parsed,
                "pi_receipt_path": str(result.receipt_path),
            },
        )
        return parsed
    raise RuntimeError("tree judgment loop did not return")


def _judgment(
    kind: str,
    payload: dict[str, Any],
    expected_fields: set[str],
    cache: dict[str, Any],
    config: ExperimentConfig,
    output: Path,
    pi_runner: PiRunner,
    stats: dict[str, int],
) -> dict[str, Any]:
    key = kind + ":" + canonical_json_hash(payload)
    if key in cache:
        stats["cache_hits"] += 1
    else:
        stats["judgments"] += 1
    return _cached_judgment(
        kind, payload, expected_fields, cache, config, output, pi_runner
    )


def _same_purpose(
    episode: dict[str, Any],
    node: dict[str, Any],
    cache: dict[str, Any],
    config: ExperimentConfig,
    output: Path,
    pi_runner: PiRunner,
    stats: dict[str, int],
) -> bool:
    compared = [
        {"purpose": episode["purpose"], "actions": episode["what_it_did"]},
        {
            "purpose": node["purpose"],
            "actions": " | ".join(
                variant["text"] for variant in node["what_it_did_variants"]
            )[:500],
        },
    ]
    compared.sort(key=lambda item: json.dumps(item, sort_keys=True))
    return _judgment(
        "same-purpose",
        {"criteria": "concrete-purpose-v2", "episodes": compared},
        {"same", "reason"},
        cache,
        config,
        output,
        pi_runner,
        stats,
    )["same"]


def _broaden_purpose(
    current: str,
    new: str,
    cache: dict[str, Any],
    config: ExperimentConfig,
    output: Path,
    pi_runner: PiRunner,
    stats: dict[str, int],
) -> str | None:
    if current.strip() == new.strip():
        return current
    judgment = _judgment(
        "broaden-purpose",
        {
            "criteria": "conservative-purpose-v2",
            "current_purpose": current,
            "new_purpose": new,
        },
        {"mergeable", "purpose", "reason"},
        cache,
        config,
        output,
        pi_runner,
        stats,
    )
    if not judgment["mergeable"]:
        return None
    return judgment["purpose"].strip()


def _same_approach(
    left: str,
    right: str,
    cache: dict[str, Any],
    config: ExperimentConfig,
    output: Path,
    pi_runner: PiRunner,
    stats: dict[str, int],
) -> bool:
    return _judgment(
        "same-approach",
        {
            "criteria": "concrete-approach-v2",
            "ways": sorted([left.strip(), right.strip()]),
        },
        {"same"},
        cache,
        config,
        output,
        pi_runner,
        stats,
    )["same"]


def _merge_variant(
    node: dict[str, Any],
    episode: dict[str, Any],
    run_id: str,
    cache: dict[str, Any],
    config: ExperimentConfig,
    output: Path,
    pi_runner: PiRunner,
    stats: dict[str, int],
) -> None:
    for variant in node["what_it_did_variants"]:
        if _same_approach(
            episode["what_it_did"],
            variant["text"],
            cache,
            config,
            output,
            pi_runner,
            stats,
        ):
            if run_id not in variant["run_ids"]:
                variant["run_ids"].append(run_id)
            return
    node["what_it_did_variants"].append(
        {"text": episode["what_it_did"], "run_ids": [run_id]}
    )


def merge_episodes(
    tree: dict[str, Any],
    episodes: list[dict[str, Any]],
    run_id: str,
    failures: list[dict[str, str]],
    merge_cache: dict[str, Any],
    config: ExperimentConfig,
    output_dir: str | Path,
    *,
    run_meta: dict[str, str] | None = None,
    pi_runner: PiRunner = run_pi,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fold one episode line and return the validated tree and updated cache."""
    merged = validate_tree(tree)
    if not isinstance(merge_cache, dict):
        raise ValueError("tree merge cache must be an object")
    cache = json.loads(json.dumps(merge_cache))
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be nonempty")
    if run_id in merged["runs"]:
        raise ValueError("duplicate membership for an existing run")
    metadata = run_meta or {}
    if not isinstance(metadata, dict) or any(
        not isinstance(key, str)
        or not key
        or not isinstance(value, str)
        for key, value in metadata.items()
    ):
        raise ValueError("run metadata must contain string fields")
    line = _validate_input_episodes(episodes)
    episode_ids = {episode["episode_id"] for episode in line}
    failures_by_episode = {episode_id: [] for episode_id in episode_ids}
    for failure in failures:
        if (
            not isinstance(failure, dict)
            or set(failure) != {"failure_id", "episode_id"}
            or failure.get("episode_id") not in episode_ids
            or not isinstance(failure.get("failure_id"), str)
            or not failure["failure_id"]
        ):
            raise ValueError("failure link is invalid")
        failures_by_episode[failure["episode_id"]].append(failure["failure_id"])
    merged["runs"][run_id] = dict(metadata)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    parent_id: str | None = None
    previous_outcome: str | None = None
    created = 0
    merged_count = 0
    stats = {"judgments": 0, "cache_hits": 0}
    for episode in line:
        children = (
            merged["root_children"]
            if parent_id is None
            else merged["nodes"][parent_id]["children"]
        )
        node_id: str | None = None
        admitted_purpose: str | None = None
        for child_id in children:
            if _same_purpose(
                episode,
                merged["nodes"][child_id],
                cache,
                config,
                output,
                pi_runner,
                stats,
            ):
                candidate_purpose = _broaden_purpose(
                    merged["nodes"][child_id]["purpose"],
                    episode["purpose"],
                    cache,
                    config,
                    output,
                    pi_runner,
                    stats,
                )
                if candidate_purpose is None:
                    continue
                node_id = child_id
                admitted_purpose = candidate_purpose
                break
        if node_id is None:
            node_id = _new_node(merged, episode, run_id)
            created += 1
        else:
            if admitted_purpose is None:
                raise RuntimeError("admitted tree merge has no concrete purpose")
            merged_count += 1
            node = merged["nodes"][node_id]
            _add_member(merged, node_id, episode, run_id)
            node["purpose"] = admitted_purpose
            _merge_variant(
                node,
                episode,
                run_id,
                cache,
                config,
                output,
                pi_runner,
                stats,
            )
        if not merged["nodes"][node_id]["members"]:
            _add_member(merged, node_id, episode, run_id)
        for failure_id in failures_by_episode[episode["episode_id"]]:
            if failure_id not in merged["nodes"][node_id]["failure_ids"]:
                merged["nodes"][node_id]["failure_ids"].append(failure_id)
        _link(
            merged,
            parent_id,
            node_id,
            run_id,
            previous_outcome,
            episode["opening_reasoning"],
        )
        parent_id = node_id
        previous_outcome = episode["outcome"]
    validated = validate_tree(merged)
    atomic_write_json(output / "tree.json", validated)
    atomic_write_json(output / "tree-merge-cache.json", cache)
    atomic_write_json(
        output / "tree-merge.json",
        {
            "schema": "skillrace-tree-merge/2",
            "run_id": run_id,
            "node_count": len(validated["nodes"]),
            "created_node_count": created,
            "merged_episode_count": merged_count,
            "branch_count": (
                int(len(validated["root_children"]) > 1)
                + sum(
                    1
                    for node in validated["nodes"].values()
                    if len(node["children"]) > 1
                )
            ),
            "transition_count": (
                sum(len(items) for items in validated["root_edges"].values())
                + sum(
                    len(items)
                    for node in validated["nodes"].values()
                    for items in node["edges"].values()
                )
            ),
            "judgment_count": stats["judgments"],
            "cache_hit_count": stats["cache_hits"],
            "judgment_evidence_paths": [
                str(path)
                for path in sorted((output / "judgments").glob("**/judgment.json"))
            ] if (output / "judgments").is_dir() else [],
        },
    )
    return validated, cache
