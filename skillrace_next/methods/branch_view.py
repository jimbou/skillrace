import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
from typing import Any

from .reasoning_tree import validate_tree


def edge_id(edge: dict[str, Any]) -> str:
    source = edge.get("source_node_id")
    target = edge.get("target_node_id")
    if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
        raise ValueError("edge endpoints must be nonempty strings")
    digest = hashlib.sha256(f"{source}\0{target}".encode("utf-8")).hexdigest()
    return "edge-" + digest[:16]


def _stable_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def build_edge_index(tree: dict[str, Any]) -> list[dict[str, Any]]:
    validated = validate_tree(tree)
    nodes = validated["nodes"]
    result: list[dict[str, Any]] = []
    pairs = [
        (source_id, target_id, nodes[source_id]["edges"][target_id])
        for source_id, node in nodes.items()
        for target_id in node["children"]
    ]
    for source_id, target_id, transitions in sorted(
        pairs,
        key=lambda item: edge_id(
            {"source_node_id": item[0], "target_node_id": item[1]}
        ),
    ):
        result.append(
            {
                "edge_id": edge_id(
                    {"source_node_id": source_id, "target_node_id": target_id}
                ),
                "source": nodes[source_id]["purpose"],
                "target": nodes[target_id]["purpose"],
                "reasoning": _stable_unique(
                    [transition["reasoning"] for transition in transitions]
                ),
                "previous_outcomes": _stable_unique(
                    [
                        transition["in_outcome"]
                        for transition in transitions
                        if transition["in_outcome"] is not None
                    ]
                ),
                "transitions": len(transitions),
                "failures": len(nodes[target_id]["failure_ids"]),
            }
        )
    if not result:
        raise ValueError("reasoning tree has no observed episode-to-episode edge")
    return sorted(result, key=lambda item: (-item["failures"], item["edge_id"]))


def isolate_branch(tree: dict[str, Any], selected_edge_id: str) -> dict[str, Any]:
    validated = validate_tree(tree)
    nodes = validated["nodes"]
    internal_edges = {
        edge_id({"source_node_id": source_id, "target_node_id": target_id}): (
            source_id,
            target_id,
        )
        for source_id, node in nodes.items()
        for target_id in node["children"]
    }
    if selected_edge_id not in internal_edges:
        raise ValueError("selected edge is not an observed episode-to-episode edge")
    source_id, target_id = internal_edges[selected_edge_id]
    parents: dict[str, str | None] = {
        child_id: None for child_id in validated["root_children"]
    }
    queue = deque(validated["root_children"])
    while queue:
        current = queue.popleft()
        for child_id in nodes[current]["children"]:
            if child_id in parents:
                raise ValueError("reasoning tree does not have a unique branch path")
            parents[child_id] = current
            queue.append(child_id)
    if source_id not in parents:
        raise ValueError("selected edge is not reachable from root")
    reversed_path = [source_id]
    while parents[reversed_path[-1]] is not None:
        reversed_path.append(parents[reversed_path[-1]])
    path_nodes = list(reversed(reversed_path)) + [target_id]
    transition_views: list[dict[str, Any]] = []
    first = path_nodes[0]
    transition_views.append(
        {
            "edge_id": edge_id(
                {"source_node_id": "root", "target_node_id": first}
            ),
            "source_node_id": "root",
            "target_node_id": first,
            "transitions": validated["root_edges"][first],
        }
    )
    for parent_id, child_id in zip(path_nodes, path_nodes[1:]):
        transition_views.append(
            {
                "edge_id": edge_id(
                    {"source_node_id": parent_id, "target_node_id": child_id}
                ),
                "source_node_id": parent_id,
                "target_node_id": child_id,
                "transitions": nodes[parent_id]["edges"][child_id],
            }
        )
    card = next(
        item for item in build_edge_index(validated)
        if item["edge_id"] == selected_edge_id
    )
    return {
        "schema": "skillrace-branch-view/2",
        "target_edge": card,
        "path": [
            {
                "node_id": node_id,
                "purpose": nodes[node_id]["purpose"],
                "member_outcomes": _stable_unique(
                    [member["outcome"] for member in nodes[node_id]["members"]]
                ),
                "failure_ids": nodes[node_id]["failure_ids"],
                "runs": nodes[node_id]["runs"],
            }
            for node_id in path_nodes
        ],
        "reasoning_edges": transition_views,
    }


def compact_branch_for_prompt(branch: dict[str, Any]) -> dict[str, Any]:
    if branch.get("schema") != "skillrace-branch-view/2":
        raise ValueError("branch prompt requires skillrace-branch-view/2")
    path = branch.get("path")
    edges = branch.get("reasoning_edges")
    if not isinstance(path, list) or not isinstance(edges, list):
        raise ValueError("branch prompt requires path and reasoning edges")
    return {
        "schema": "skillrace-branch-prompt/1",
        "target_edge": branch["target_edge"],
        "path_node_count": len(path),
        "path": [
            {
                "node_id": node["node_id"],
                "purpose": node["purpose"],
                "member_outcomes": node["member_outcomes"],
                "failure_ids": node["failure_ids"],
                "run_count": len(node["runs"]),
            }
            for node in path
        ],
        "reasoning_edges": [
            {
                "edge_id": edge["edge_id"],
                "source_node_id": edge["source_node_id"],
                "target_node_id": edge["target_node_id"],
                "reasoning": _stable_unique(
                    [transition["reasoning"] for transition in edge["transitions"]]
                ),
                "previous_outcomes": _stable_unique(
                    [
                        transition["in_outcome"]
                        for transition in edge["transitions"]
                        if transition["in_outcome"] is not None
                    ]
                ),
                "transitions": len(edge["transitions"]),
            }
            for edge in edges
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", required=True)
    parser.add_argument("--edge-id", required=True)
    args = parser.parse_args()
    tree = json.loads(Path(args.tree).read_text(encoding="utf-8"))
    print(json.dumps(isolate_branch(tree, args.edge_id), sort_keys=True))


if __name__ == "__main__":
    main()
