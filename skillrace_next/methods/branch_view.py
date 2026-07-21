import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
from typing import Any


def edge_id(edge: dict[str, Any]) -> str:
    source = edge.get("source_node_id")
    target = edge.get("target_node_id")
    if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
        raise ValueError("edge endpoints must be nonempty strings")
    digest = hashlib.sha256(f"{source}\0{target}".encode("utf-8")).hexdigest()
    return "edge-" + digest[:16]


def build_edge_index(tree: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = {node["node_id"]: node for node in tree["nodes"]}
    result: list[dict[str, Any]] = []
    for edge in sorted(tree["edges"], key=edge_id):
        source_id = edge["source_node_id"]
        if source_id == "root":
            continue
        source = nodes[source_id]
        target = nodes[edge["target_node_id"]]
        result.append(
            {
                "edge_id": edge_id(edge),
                "source": source["purpose"],
                "reasoning": edge["reason"],
                "target": target["purpose"],
                "outcome": target["outcome"],
                "observations": len(target["member_run_ids"]),
                "failures": len(target["failure_ids"]),
            }
        )
    if not result:
        raise ValueError("reasoning tree has no observed episode-to-episode edge")
    return result


def isolate_branch(tree: dict[str, Any], selected_edge_id: str) -> dict[str, Any]:
    nodes = {node["node_id"]: node for node in tree["nodes"]}
    edges = {edge_id(edge): edge for edge in tree["edges"]}
    if selected_edge_id not in edges or edges[selected_edge_id]["source_node_id"] == "root":
        raise ValueError("selected edge is not an observed episode-to-episode edge")
    selected = edges[selected_edge_id]
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in tree["edges"]:
        outgoing.setdefault(edge["source_node_id"], []).append(edge)
    for values in outgoing.values():
        values.sort(key=edge_id)
    queue: deque[tuple[str, list[str], list[dict[str, Any]]]] = deque(
        [("root", ["root"], [])]
    )
    visited = {"root"}
    source_id = selected["source_node_id"]
    found_nodes: list[str] | None = None
    found_edges: list[dict[str, Any]] | None = None
    while queue:
        current, node_path, edge_path = queue.popleft()
        if current == source_id:
            found_nodes = node_path
            found_edges = edge_path
            break
        for edge in outgoing.get(current, []):
            target = edge["target_node_id"]
            if target in visited:
                continue
            visited.add(target)
            queue.append((target, [*node_path, target], [*edge_path, edge]))
    if found_nodes is None or found_edges is None:
        raise ValueError("selected edge is not reachable from root")
    target_id = selected["target_node_id"]
    path_nodes = [*found_nodes, target_id]
    path_edges = [*found_edges, selected]
    card = next(item for item in build_edge_index(tree) if item["edge_id"] == selected_edge_id)
    return {
        "schema": "skillrace-branch-view/1",
        "target_edge": card,
        "path": [
            {
                "node_id": node_id,
                "purpose": nodes[node_id]["purpose"],
                "outcome": nodes[node_id]["outcome"],
                "failure_ids": nodes[node_id]["failure_ids"],
            }
            for node_id in path_nodes
        ],
        "reasoning_edges": [
            {
                "edge_id": edge_id(edge),
                "source_node_id": edge["source_node_id"],
                "target_node_id": edge["target_node_id"],
                "reason": edge["reason"],
            }
            for edge in path_edges
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
