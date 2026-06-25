"""Component 4 — Tree Builder (minimal v1): fold ONE run's episode line into the global
behavior tree. That is the ONLY job of this step.

  input : the global tree (a JSON file; created empty if missing)
        + one run's episodes (the segmenter's output: a list with start_call/intent/
          what_it_did/outcome)
        + that run's session.jsonl (the AGENT-UNDER-TEST trace, raw/session.jsonl) — used
          ONLY to attach each episode's `opening_reasoning` (the reasoning before its first
          tool call, which leads from the previous episode into this one).
  output: the updated global tree (same JSON file).

Algorithm (prefix-merge, contextual children) — see docs/design/tree-builder.md:
  node = ROOT (its children are the level-0 nodes)
  for each episode e in the run's line, top→down:
    among the CHILDREN of the current node, find one with the SAME PURPOSE as e
    (a cheap model judgment — merge even if done a slightly different way; purpose is
    what matters; OUTCOME is ignored).
      match  -> merge e into it (add member), record the edge (prev outcome + e's
                opening_reasoning), descend into it.
      none   -> create a new child node, link it, descend. (Its children are empty, so
                every later episode also creates a fresh node — the rest of the line
                grafts on as a new branch automatically.)

No split / frontier / guard synthesis here — those are later components.

Which run an episode belongs to: the run id is derived from the run directory (the
parent of `raw/`) — so `--session runs/<run>/raw/session.jsonl` gives `run_id = <run>`.
That id is recorded on every member and every edge, and registered (id -> dir) in
`tree["runs"]`. Override it with `--run-id` / `--run-dir` if the layout differs.

Usage:
  # run id auto-derived as "mcp-tools-resources" (the run dir name):
  python -m skillrace.tree \
    --episodes runs/mcp-tools-resources/episodes.json \
    --session  runs/mcp-tools-resources/raw/session.jsonl \
    --tree     out/skillrace/mcp-server-patterns/tree.json

  # or name the run explicitly:
  python -m skillrace.tree --run-id mcp-001 \
    --episodes runs/mcp-tools-resources/episodes.json \
    --session  runs/mcp-tools-resources/raw/session.jsonl \
    --tree     out/skillrace/mcp-server-patterns/tree.json
"""
from __future__ import annotations
import argparse
import hashlib
import json
import pathlib

from .closeai import chat, extract_json
from .simplify_trace import call_reasonings

SAME_PURPOSE_SYS = (
    "You decide whether two episodes of a coding-agent run pursue the SAME PURPOSE — the "
    "same sub-goal — EVEN IF they went about it in a slightly different way or used "
    "different commands. What matters is the PURPOSE/intent; do NOT require the exact "
    "actions to match, and IGNORE how each turned out (the outcome). Answer `same:true` "
    "if their purpose is essentially the same sub-goal, `same:false` if they are different "
    "sub-goals.\nReturn ONLY JSON: {\"same\": true|false, \"reason\": \"...\"}."
)

BROADEN_SYS = (
    "Two episodes of a coding-agent run were judged to share the same PURPOSE and merged "
    "into one tree node. Given the node's current purpose and the newly merged episode's "
    "purpose, write ONE concise purpose statement that GENERALIZES over BOTH — it must "
    "still cover each of them (broaden only; never narrow to just one, never drop what "
    "either was about). Keep it a short phrase.\nReturn ONLY JSON: {\"intent\": \"...\"}."
)

SAME_APPROACH_SYS = (
    "Two episodes pursued the SAME purpose. You now decide whether they did it the SAME "
    "WAY — the same approach/method — or a meaningfully DIFFERENT way. Minor wording or "
    "incidental detail differences do NOT count as different; a genuinely different method "
    "does. Return ONLY JSON: {\"same\": true|false}."
)


# ----------------------------------------------------------------- episodes I/O

def load_episodes(path):
    obj = json.loads(pathlib.Path(path).read_text())
    eps = obj["episodes"] if isinstance(obj, dict) and "episodes" in obj else obj
    if not isinstance(eps, list):
        raise ValueError("episodes file has no episode list")
    return eps


def attach_opening_reasoning(eps, session_path):
    """Set each episode's `opening_reasoning` from the run's session trace: the reasoning
    of the assistant message that owns the episode's first tool call (start_call)."""
    reasonings = call_reasonings(session_path)
    for e in eps:
        sc = e.get("start_call")
        e["opening_reasoning"] = (reasonings[sc - 1]
                                  if isinstance(sc, int) and 1 <= sc <= len(reasonings) else "")
    return eps


# ----------------------------------------------------------------- merge judgment

def _pair_key(a_intent, a_did, b_intent, b_did):
    """Order-independent key for caching a same-purpose verdict."""
    x = f"{a_intent}\n{a_did}".strip()
    y = f"{b_intent}\n{b_did}".strip()
    lo, hi = sorted([x, y])
    return hashlib.sha1((lo + "\n@@@\n" + hi).encode()).hexdigest()


def _node_did(node):
    """A short representative of how this node has been done (its distinct variants)."""
    return " | ".join(v["text"] for v in node.get("what_it_did_variants", []))[:500]


def same_purpose(ep, node, model, cache):
    """Cached model judgment: does episode `ep` pursue the same purpose as `node`?
    Decided primarily on the (broadened) intent; how it was done is secondary context."""
    node_did = _node_did(node)
    key = _pair_key(ep.get("intent", ""), ep.get("what_it_did", ""),
                    node.get("intent", ""), node_did)
    if key in cache:
        return cache[key]["same"]
    user = (f"Episode A:\n  intent: {ep.get('intent','')}\n  what_it_did: {ep.get('what_it_did','')}\n\n"
            f"Episode B:\n  intent: {node.get('intent','')}\n  what_it_did: {node_did}\n\n"
            "Do A and B pursue the same purpose? Return ONLY the JSON.")
    resp = chat([{"role": "system", "content": SAME_PURPOSE_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, reasoning=True, max_tokens=400,
                tag="tree.same_purpose")
    obj = extract_json(resp["content"])
    cache[key] = {"same": bool(obj.get("same")), "reason": obj.get("reason", "")}
    return cache[key]["same"]


def broaden_intent(cur, new, model, cache):
    """Generalize the node's purpose (the merge KEY) to also cover a newly merged
    episode's purpose. Broaden only. Cached for consistency."""
    if not new or new.strip() == cur.strip():
        return cur
    key = "b:" + hashlib.sha1((cur + "\n@@@\n" + new).encode()).hexdigest()
    if key in cache:
        return cache[key]["intent"]
    user = (f"Node's current purpose: {cur}\nNewly merged episode's purpose: {new}\n\n"
            "Return ONLY the JSON with the generalized purpose.")
    resp = chat([{"role": "system", "content": BROADEN_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, reasoning=True, max_tokens=300,
                tag="tree.broaden")
    intent = (extract_json(resp["content"]).get("intent") or cur).strip()
    cache[key] = {"intent": intent}
    return intent


def same_approach(a_did, b_did, model, cache):
    """Cached judgment: did two episodes pursue the shared purpose the SAME WAY?"""
    lo, hi = sorted([a_did.strip(), b_did.strip()])
    key = "a:" + hashlib.sha1((lo + "\n@@@\n" + hi).encode()).hexdigest()
    if key in cache:
        return cache[key]["same"]
    user = f"Way A: {a_did}\n\nWay B: {b_did}\n\nSame approach? Return ONLY the JSON."
    resp = chat([{"role": "system", "content": SAME_APPROACH_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, reasoning=True, max_tokens=200,
                tag="tree.same_approach")
    cache[key] = {"same": bool(extract_json(resp["content"]).get("same"))}
    return cache[key]["same"]


# ----------------------------------------------------------------- the tree

def empty_tree(skill=None):
    return {"schema": "behavior-tree/2", "skill": skill,
            "runs": {},                 # run_id -> {dir, session, episodes}
            "next_id": 0,
            "root_children": [],        # level-0 node ids (children of the virtual ROOT)
            "root_edges": {},           # node_id -> [ {run, in_outcome:null, reasoning} ]  (entry edges)
            "nodes": {}}


def _new_node(tree, ep, run_id):
    nid = f"n{tree['next_id']}"
    tree["next_id"] += 1
    did = ep.get("what_it_did", "")
    tree["nodes"][nid] = {
        "id": nid,
        "intent": ep.get("intent", ""),          # broadened purpose (the merge key)
        # distinct ways this purpose was pursued; similar ones collapse, different add:
        "what_it_did_variants": [{"text": did, "runs": [run_id]}] if did else [],
        "runs": [],                               # which run(s) this node belongs to (post-merge)
        "members": [],                            # every episode merged into this node
        "children": [],
        "edges": {},                              # child_id -> [ {run, in_outcome, reasoning} ]
    }
    return nid


def _add_member(tree, nid, ep, run_id, ep_index):
    node = tree["nodes"][nid]
    node["members"].append({
        "run": run_id, "episode_index": ep_index,
        "intent": ep.get("intent", ""), "what_it_did": ep.get("what_it_did", ""),
        "outcome": ep.get("outcome", ""), "opening_reasoning": ep.get("opening_reasoning", ""),
    })
    if run_id not in node["runs"]:                # a node can belong to several runs after merge
        node["runs"].append(run_id)


def _merge_variant(node, ep, run_id, model, cache):
    """Add this episode's `what_it_did` to the node's distinct-approach variants:
    if it's the SAME WAY as an existing variant, just record the run there; if it's a
    genuinely DIFFERENT way, add it as a new variant."""
    did = ep.get("what_it_did", "")
    if not did:
        return
    for v in node["what_it_did_variants"]:
        if same_approach(did, v["text"], model, cache):
            if run_id not in v["runs"]:
                v["runs"].append(run_id)
            return
    node["what_it_did_variants"].append({"text": did, "runs": [run_id]})


def _children(tree, node_id):
    return tree["root_children"] if node_id is None else tree["nodes"][node_id]["children"]


def _link(tree, parent_id, child_id, run_id, in_outcome, reasoning):
    """Link parent->child and record the transition edge, PER RUN. The ROOT case
    (parent_id is None) is an edge too — stored in tree['root_edges'] — so every
    transition a run took is available as an edge, including the entry into level 0."""
    rec = {"run": run_id, "in_outcome": in_outcome, "reasoning": reasoning}
    if parent_id is None:
        if child_id not in tree["root_children"]:
            tree["root_children"].append(child_id)
        tree["root_edges"].setdefault(child_id, []).append(rec)
        return
    pnode = tree["nodes"][parent_id]
    if child_id not in pnode["children"]:
        pnode["children"].append(child_id)
    pnode["edges"].setdefault(child_id, []).append(rec)


def fold(tree, eps, run_id, model, cache, run_meta=None):
    """Fold one run's episode line into the tree. Returns a list of per-episode actions.
    `run_meta` (dir/session/episodes paths) is recorded in the tree's run registry."""
    tree["runs"][run_id] = run_meta or tree["runs"].get(run_id, {})
    actions = []
    node_id = None            # virtual ROOT
    prev_outcome = None
    for i, ep in enumerate(eps):
        match = None
        for cid in _children(tree, node_id):
            if same_purpose(ep, tree["nodes"][cid], model, cache):
                match = cid
                break
        if match is not None:
            node = tree["nodes"][match]
            _add_member(tree, match, ep, run_id, i)
            # broaden the PURPOSE (the merge key); record the WAY as a variant
            node["intent"] = broaden_intent(node["intent"], ep.get("intent", ""), model, cache)
            _merge_variant(node, ep, run_id, model, cache)
            _link(tree, node_id, match, run_id, prev_outcome, ep.get("opening_reasoning", ""))
            actions.append(("merge", match, ep.get("intent", "")))
            node_id = match
        else:
            new_id = _new_node(tree, ep, run_id)
            _add_member(tree, new_id, ep, run_id, i)
            _link(tree, node_id, new_id, run_id, prev_outcome, ep.get("opening_reasoning", ""))
            actions.append(("new", new_id, ep.get("intent", "")))
            node_id = new_id
        prev_outcome = ep.get("outcome", "")
    return actions


# ----------------------------------------------------------------- pretty print

def _print_tree(tree):
    def walk(nid, depth, seen):
        n = tree["nodes"][nid]
        branch = " «BRANCH»" if len(n["children"]) > 1 else ""
        nv = len(n.get("what_it_did_variants", []))
        ways = f", {nv} ways" if nv > 1 else ""
        runs = ",".join(n.get("runs", []))
        print(f"{'  '*depth}• [{nid}] {n['intent'][:66]}  (runs: {runs}{ways}){branch}")
        for cid in n["children"]:
            walk(cid, depth + 1, seen)
    for rid in tree["root_children"]:
        walk(rid, 0, set())


def main():
    ap = argparse.ArgumentParser(description="Fold one run's episodes into the global behavior tree")
    ap.add_argument("--episodes", required=True, help="segmenter output (episodes json)")
    ap.add_argument("--session", required=True, help="the run's raw/session.jsonl (agent-under-test)")
    ap.add_argument("--tree", required=True, help="global tree json (created if missing)")
    ap.add_argument("--run-id", help="run id (default: the run dir name)")
    ap.add_argument("--run-dir", help="run directory (default: the session's parent run dir)")
    ap.add_argument("--skill", help="skill name (recorded on a fresh tree)")
    ap.add_argument("--model", default="qwen3.6-flash")
    args = ap.parse_args()

    session = pathlib.Path(args.session)
    run_dir = pathlib.Path(args.run_dir) if args.run_dir else session.resolve().parent.parent
    run_id = args.run_id or run_dir.name
    run_meta = {"dir": str(run_dir), "session": str(session), "episodes": str(args.episodes)}

    eps = attach_opening_reasoning(load_episodes(args.episodes), session)

    tree_path = pathlib.Path(args.tree)
    tree = json.loads(tree_path.read_text()) if tree_path.exists() else empty_tree(args.skill)
    cache_path = tree_path.with_suffix(".cache.json")
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    before = len(tree["nodes"])
    actions = fold(tree, eps, run_id, args.model, cache, run_meta=run_meta)

    tree_path.parent.mkdir(parents=True, exist_ok=True)
    tree_path.write_text(json.dumps(tree, indent=2))
    cache_path.write_text(json.dumps(cache, indent=2))

    merged = sum(1 for a in actions if a[0] == "merge")
    created = sum(1 for a in actions if a[0] == "new")
    print(f"folded run {run_id!r}: {len(eps)} episodes -> {merged} merged, {created} new "
          f"(tree nodes {before} -> {len(tree['nodes'])})\n")
    for kind, nid, intent in actions:
        print(f"  {'MERGE ' if kind=='merge' else 'NEW   '}[{nid}] {intent[:72]}")
    print("\nGLOBAL TREE:")
    _print_tree(tree)
    print(f"\nwrote {tree_path}")


if __name__ == "__main__":
    main()
