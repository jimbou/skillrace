"""Component 5 — Guards & test synthesis (the conceptual core).

A BRANCH is a tree node whose children diverge: runs that reached the same
situation went different ways. The GUARD is the condition that explains the split,
read from two distinct signals (never conflated):
  A) the OUTCOME of the episode that just finished — the edge's `in_outcome`,
     grounded in tool outputs, and
  B) the OPENING REASONING of the next episode — the edge's `reasoning`, the
     agent's stated why.
When A and B disagree (the outcome observably failed but the reasoning proceeds as
if it passed) that is recorded as a bug signal, not reconciled away.

The loop over a campaign:
  1. find_branches(tree)          — where guards live
  2. extract_guard(...)           — model distills condition + executable grounding
  3. build_frontier(...)          — untried mutations (negations / novel siblings)
  4. select_target(...)           — PROPERTY-GUIDED: given the skill's properties,
                                    pick the mutation most likely to drive the skill
                                    toward a property violation (feasible from E0)
  5. synthesize(...)              — draft (task, env, validation script), realize a
                                    concrete (prompt, Containerfile tail) via the
                                    SHARED realize pipeline, build it
  6. validate(...)                — run the validation script in the built container
                                    with NO agent; repair the tail on failure
Only a validated candidate is handed to the runner ("LLM proposes, checker
disposes"). Guards only checkable mid-run (decidable_from=agent_runtime) are
deferred and counted, per the tex.

State lives NEXT TO the tree: <tree>.guards.json holds extracted guards and which
mutations have been tried, so the frontier survives across loop iterations.

Usage (one synthesis step; the loop calls these as functions):
  python -m skillrace.guards --tree out/skillrace/<skill>/tree.json \
      --skill-dir skills/<skill> --base skillrace/<skill>:base \
      --props skills/<skill>/properties.json --out out/skillrace/<skill>/cases
"""
from __future__ import annotations
import argparse
import json
import pathlib
import re
import subprocess
import uuid

from .ablations import guard_view
from .closeai import chat, extract_json
from .generator import (
    DEFAULT_BUILD_RETRIES,
    DEFAULT_BUILD_TIMEOUT,
    realize,
    realize_and_build,
    skill_context,
)
from .io_utils import atomic_write_json

GUARD_SYS = (
    "You analyze a BRANCH in a coding-agent behavior tree: runs that reached the "
    "same situation (node) continued into DIFFERENT next episodes. Distill the "
    "GUARD — the condition that explains which way a run went. You get, per side: "
    "the OUTCOME of the episode that just finished (grounded in tool outputs), the "
    "agent's OPENING REASONING entering that side, and the initial-environment "
    "description of the run(s) on that side. The guard is usually about the STATE "
    "the agent found (what the environment provided, how something failed), not "
    "about tactics.\n"
    "Also flag DISAGREEMENTS: any side where the opening reasoning proceeds as if "
    "the prior outcome were different from what the tool outputs show (e.g. output "
    "shows a failure, reasoning treats it as passing) — that is a bug signal.\n"
    "Prefer an EXECUTABLE grounding: a bash check runnable in the INITIAL container "
    "(exit 0 = condition holds), e.g. `test -f x`, `pytest -q 2>&1 | grep -q "
    "ImportError`. Set decidable_from='E0' ONLY if the condition is decidable from "
    "the initial setup alone (before any agent step); otherwise 'agent_runtime'.\n"
    "Return ONLY JSON:\n"
    "{\"condition\": \"...\",\n"
    " \"grounding\": {\"kind\": \"executable|nl\", \"check\": \"...bash or null...\","
    " \"decidable_from\": \"E0|agent_runtime\"},\n"
    " \"value_space\": {\"type\": \"binary|multivalued\","
    " \"observed\": [\"...one per side...\"],"
    " \"unobserved_siblings\": [\"...feasible values NOT yet seen...\"]},\n"
    " \"disagreements\": [{\"side\": N, \"note\": \"...\"}]}"
)

GUARD_OUTCOMES_SYS = (
    "You analyze a BRANCH in a coding-agent behavior tree: runs that reached the "
    "same situation continued into different next episodes. You receive only the "
    "prior tool-grounded OUTCOME and initial-environment description for each side. "
    "Distill the state condition that best explains which way a run went. Prefer an "
    "EXECUTABLE grounding runnable in the initial container (exit 0 = condition "
    "holds). Set decidable_from='E0' only when initial setup alone decides it; "
    "otherwise use 'agent_runtime'. Return ONLY JSON:\n"
    "{\"condition\": \"...\", \"grounding\": {\"kind\": \"executable|nl\", "
    "\"check\": \"...bash or null...\", \"decidable_from\": "
    "\"E0|agent_runtime\"}, \"value_space\": {\"type\": "
    "\"binary|multivalued\", \"observed\": [\"...one per side...\"], "
    "\"unobserved_siblings\": [\"...feasible unseen values...\"]}, "
    "\"disagreements\": []}"
)

SELECT_SYS = (
    "You pick the next test to synthesize for a coding-agent skill. You get the "
    "skill's CORRECTNESS PROPERTIES and a FRONTIER of branch guards, each with "
    "observed values and untried mutations (negations / unobserved siblings). "
    "Choose the ONE (frontier item, mutation) whose exploration is MOST LIKELY to "
    "drive the skill toward VIOLATING one of the properties — i.e. if the guard "
    "took this other (feasible!) value, the skill's guidance could plausibly "
    "mishandle it in a way a property would catch. The mutation must be something "
    "an initial environment can set up (no mid-run state).\n"
    "Return ONLY JSON: {\"frontier_index\": N, \"mutation\": \"...\", "
    "\"targeted_property\": \"<property id>\", \"rationale\": \"...\"}"
)

SYNTH_SYS = (
    "You draft ONE new test case for a coding-agent skill, targeting a specific "
    "guard mutation. You get: the path of situations leading to the branch, the "
    "guard, its observed value(s), and the TARGET value the new test must realize "
    "in its INITIAL environment. Produce:\n"
    "  task — the natural-language task to ask the agent (consistent with the "
    "skill's purpose and with reaching this branch),\n"
    "  env  — a natural-language description of the starting environment that makes "
    "the guard take the TARGET value while still requiring the path to the branch,\n"
    "The test may change multiple coherent environment features when that creates a "
    "realistic unsolved case; it need not minimally isolate only the named guard. "
    "Reaching the intended branch is diagnostic evidence, not a requirement for the "
    "test to count. A generated test is still valuable if execution instead exposes "
    "a different branch or a confirmed defect.\n"
    "  validate_sh — a bash script that will run in the FRESHLY BUILT container "
    "(NO agent has run): exit 0 iff the TARGET condition genuinely holds in the "
    "initial state (e.g. the test really fails with the targeted error).\n"
    "Return ONLY JSON: {\"task\": \"...\", \"env\": \"...\", \"validate_sh\": \"...\"}"
)


# ------------------------------------------------------------------ branches

def find_branches(tree):
    """Every point with ≥2 out-edges: the virtual root (parent_id None) or a node."""
    out = []
    if len(tree["root_children"]) >= 2:
        out.append({"parent_id": None, "children": list(tree["root_children"])})
    for nid, n in tree["nodes"].items():
        if len(n["children"]) >= 2:
            out.append({"parent_id": nid, "children": list(n["children"])})
    return out


def branch_key(branch):
    return (branch["parent_id"] or "ROOT") + "->" + "+".join(sorted(branch["children"]))


def _edges_into(tree, parent_id, child_id):
    recs = (tree["root_edges"] if parent_id is None
            else tree["nodes"][parent_id]["edges"]).get(child_id, [])
    return recs


def _run_env_nl(tree, run_id):
    """The initial-environment description of a run, via its case's candidate.json."""
    meta = tree.get("runs", {}).get(run_id) or {}
    try:
        run_json = json.loads((pathlib.Path(meta["dir"]) / "run.json").read_text())
        cand = json.loads((pathlib.Path(run_json["case"]) / "candidate.json").read_text())
        prov = cand.get("provenance", {})
        return prov.get("env_nl") or cand.get("summary") or ""
    except Exception:
        return ""


def _path_intents(tree, node_id):
    """Intents from the root down to node_id (inclusive) — the path context."""
    parent = {}
    for nid, n in tree["nodes"].items():
        for c in n["children"]:
            parent[c] = nid
    path, cur = [], node_id
    while cur is not None:
        path.append(tree["nodes"][cur]["intent"])
        cur = parent.get(cur)
    return list(reversed(path))


# ------------------------------------------------------------------ 5a extraction

def extract_guard(
    tree,
    branch,
    model,
    skill=None,
    *,
    signal_mode="reasoning-and-outcomes",
):
    """One model call: distill the guard at a branch from signals A + B + env NL."""
    sides = []
    for i, cid in enumerate(branch["children"], 1):
        child = tree["nodes"][cid]
        recs = _edges_into(tree, branch["parent_id"], cid)
        runs = sorted({r["run"] for r in recs})
        rec = recs[0] if recs else {}
        signal = guard_view(
            {
                "outcome": rec.get("in_outcome") or "(run start)",
                "opening_reasoning": (rec.get("reasoning") or "(none)")[:600],
                "environment": (
                    "; ".join(
                        filter(None, (_run_env_nl(tree, run) for run in runs))
                    )
                    or "(unknown)"
                ),
            },
            signal_mode=signal_mode,
        )
        lines = [
            f"SIDE {i} (next episode: {child['intent']}):",
            f"  prior outcome (from tool outputs): {signal['outcome']}",
        ]
        if "opening_reasoning" in signal:
            lines.append(f"  opening reasoning: {signal['opening_reasoning']}")
        lines.append(
            f"  initial env of run(s) {','.join(runs)}: {signal['environment']}"
        )
        sides.append("\n".join(lines))
    user = ("BRANCH at situation path:\n  " +
            " -> ".join(_path_intents(tree, branch["parent_id"]) if branch["parent_id"]
                        else ["(run start)"]) +
            "\n\n" + "\n\n".join(sides) + "\n\nReturn ONLY the JSON.")
    system_prompt = (
        GUARD_SYS
        if signal_mode == "reasoning-and-outcomes"
        else GUARD_OUTCOMES_SYS
    )
    resp = chat([{"role": "system", "content": system_prompt},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, reasoning=True, max_tokens=900,
                tag="guards.extract", skill=skill)
    g = extract_json(resp["content"])
    g["branch_key"] = branch_key(branch)
    g["parent_id"] = branch["parent_id"]
    g["children"] = branch["children"]
    return g, resp["cost_usd"]


def load_guard_state(tree_path, *, signal_mode="reasoning-and-outcomes"):
    p = pathlib.Path(str(tree_path).replace(".json", "") + ".guards.json")
    if p.exists():
        state = json.loads(p.read_text())
        stored_mode = state.get("signal_mode", "reasoning-and-outcomes")
        if stored_mode != signal_mode:
            raise ValueError("guard cache signal mode does not match strategy")
    else:
        state = {}
    state.setdefault("schema", "skillrace-guard-state/1")
    state.setdefault("signal_mode", signal_mode)
    state.setdefault("guards", {})
    state.setdefault("tried", {})
    state.setdefault("deferred", [])
    return state, p


def extract_all_guards(
    tree,
    tree_path,
    model,
    skill=None,
    *,
    signal_mode="reasoning-and-outcomes",
):
    """Extract guards for every branch not yet covered; persist next to the tree."""
    state, p = load_guard_state(tree_path, signal_mode=signal_mode)
    cost = 0.0
    for br in find_branches(tree):
        k = branch_key(br)
        if k in state["guards"]:
            continue
        g, c = extract_guard(
            tree, br, model, skill=skill, signal_mode=signal_mode
        )
        cost += c
        state["guards"][k] = g
        if g.get("grounding", {}).get("decidable_from") != "E0":
            state["deferred"].append(k)   # counted, not targeted (tex §5)
    atomic_write_json(p, state)
    return state, cost


# ------------------------------------------------------------------ frontier + selection

def build_frontier(state):
    """Untried mutations of E0-decidable guards. Negation for binary guards is the
    implicit 'NOT <observed>' sibling; multivalued guards list unobserved siblings."""
    items = []
    for k, g in state["guards"].items():
        if g.get("grounding", {}).get("decidable_from") != "E0":
            continue
        vs = g.get("value_space", {})
        mutations = list(vs.get("unobserved_siblings") or [])
        if vs.get("type") == "binary" and not mutations:
            mutations = [f"NOT({'; '.join(vs.get('observed', []))})"]
        tried = set(state["tried"].get(k, []))
        untried = [m for m in mutations if m not in tried]
        if untried:
            items.append({"branch_key": k, "guard": g, "mutations": untried})
    return items


def diverse_target_batch(
    frontier,
    *,
    limit,
    tree_version,
    epoch,
    frozen_state_hash,
):
    """Round-robin distinct branches before taking a second mutation per branch."""
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (limit, tree_version, epoch)
    ):
        raise ValueError("target batch bounds/version must be non-negative integers")
    if not isinstance(frozen_state_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", frozen_state_hash
    ):
        raise ValueError("target batch requires a frozen state SHA-256 hash")
    frontier = json.loads(json.dumps(list(frontier)))
    selected = []
    mutation_index = 0
    while len(selected) < limit:
        added = False
        for item in frontier:
            mutations = item.get("mutations") or []
            if mutation_index >= len(mutations):
                continue
            branch_key_ = item.get("branch_key")
            mutation = mutations[mutation_index]
            if not isinstance(branch_key_, str) or not isinstance(mutation, str):
                raise ValueError("malformed guard frontier target")
            selected.append(
                {
                    "kind": "target",
                    "item": item,
                    "branch_key": branch_key_,
                    "mutation": mutation,
                    "tree_version": tree_version,
                    "epoch": epoch,
                    "frozen_state_hash": frozen_state_hash,
                }
            )
            added = True
            if len(selected) == limit:
                break
        if not added:
            break
        mutation_index += 1
    while len(selected) < limit:
        selected.append(
            {
                "kind": "fallback",
                "fallback_slot": len(
                    [item for item in selected if item.get("kind") == "fallback"]
                ),
                "branch_key": None,
                "mutation": None,
                "tree_version": tree_version,
                "epoch": epoch,
                "frozen_state_hash": frozen_state_hash,
            }
        )
    return selected


def select_target(frontier, properties, model, skill=None):
    """PROPERTY-GUIDED selection: which feasible mutation could break a property?"""
    if not frontier:
        return None, 0.0
    props_txt = "\n".join(f"- [{p['id']}] {p['nl']}" for p in properties)
    items_txt = "\n\n".join(
        f"[{i}] guard: {it['guard']['condition']}\n"
        f"    observed: {'; '.join(it['guard'].get('value_space', {}).get('observed', []))}\n"
        f"    untried mutations: {'; '.join(it['mutations'])}"
        for i, it in enumerate(frontier))
    user = (f"SKILL PROPERTIES:\n{props_txt}\n\nFRONTIER:\n{items_txt}\n\n"
            "Pick the single most property-threatening feasible (item, mutation). "
            "Return ONLY the JSON.")
    resp = chat([{"role": "system", "content": SELECT_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, reasoning=True, max_tokens=500,
                tag="guards.select", skill=skill)
    sel = extract_json(resp["content"])
    idx = int(sel.get("frontier_index", 0))
    idx = max(0, min(idx, len(frontier) - 1))
    mut = sel.get("mutation") or frontier[idx]["mutations"][0]
    return {"item": frontier[idx], "mutation": mut,
            "targeted_property": sel.get("targeted_property"),
            "rationale": sel.get("rationale", "")}, resp["cost_usd"]


# ------------------------------------------------------------------ 5b + 5c synthesis

def _validate_in_image(image, validate_sh):
    """Run the validation script in a fresh container of the built image. NO agent.
    Returns (ok, output_tail)."""
    p = subprocess.run(["docker", "run", "--rm", image, "bash", "-c", validate_sh],
                       capture_output=True, text=True, timeout=300)
    out = (p.stdout + p.stderr).strip()
    return p.returncode == 0, out[-800:]


def synthesize(
    tree,
    target,
    skill,
    skill_dir,
    base_image,
    model,
    out_dir,
    *,
    requested_base_image=None,
    proposal_id=None,
    provenance=None,
):
    """Draft -> realize -> build -> VALIDATE (no agent) -> case dir. Returns
    (case_dir|None, info, cost)."""
    g = target["item"]["guard"]
    ctx = skill_context(pathlib.Path(skill_dir))
    cost = 0.0
    path = _path_intents(tree, g["parent_id"]) if g["parent_id"] else ["(run start)"]
    user = (f"{ctx}\n\nPATH TO BRANCH: {' -> '.join(path)}\n"
            f"GUARD: {g['condition']}\n"
            f"OBSERVED VALUE(S): {'; '.join(g.get('value_space', {}).get('observed', []))}\n"
            f"TARGET VALUE (the new test must realize this in E0): {target['mutation']}\n\n"
            "Return ONLY the JSON.")
    resp = chat([{"role": "system", "content": SYNTH_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, reasoning=True, max_tokens=1200,
                tag="guards.synthesize", skill=skill)
    cost += resp["cost_usd"]
    draft = extract_json(resp["content"])
    task_nl, env_nl, validate_sh = draft["task"], draft["env"], draft["validate_sh"]

    cid = proposal_id or ("cand-" + uuid.uuid4().hex[:12])
    if not isinstance(cid, str) or not cid:
        raise ValueError("proposal_id must be a nonempty string")
    try:
        artifact, build_cost, last_error = realize_and_build(
            ctx,
            task_nl,
            env_nl,
            model,
            base_image,
            cid,
            build_retries=DEFAULT_BUILD_RETRIES,
            build_timeout=DEFAULT_BUILD_TIMEOUT,
            validator=lambda image: _validate_in_image(image, validate_sh),
            repair_hint=(
                f"\n\n(The environment MUST satisfy: {target['mutation']} — "
                f"the validation script is:\n{validate_sh})"
            ),
        )
        cost = round(cost + build_cost, 12)
    except Exception as error:
        return None, {"validated": False, "error": str(error)[:400]}, cost
    if artifact is None:
        return None, {"validated": False, "error": str(last_error)[-400:]}, cost

    case = pathlib.Path(out_dir) / cid
    case.mkdir(parents=True, exist_ok=True)
    (case / "Dockerfile").write_text(artifact["containerfile"])
    (case / "validate.sh").write_text(validate_sh)
    extra_provenance = dict(provenance or {})
    candidate_provenance = {
                       **extra_provenance,
                       "source": "skillrace",
                       "requested_base_image": requested_base_image or base_image,
                       "base_image_identity": base_image,
                       "branch_key": g["branch_key"],
                       "target_parent": g.get("parent_id"),
                       "guard": g["condition"],
                       "mutation": target["mutation"],
                       "targeted_property": target.get("targeted_property"),
                       "rationale": target.get("rationale"),
                       "tree_version": target.get("tree_version"),
                       "frozen_state_hash": target.get("frozen_state_hash"),
                       "validation": {
                           "validated": True,
                           "validate_sh": validate_sh,
                           "target_condition": target["mutation"],
                       },
                       "epoch": target.get("epoch", extra_provenance.get("epoch")),
                       "task_nl": task_nl, "env_nl": env_nl,
                       "attempts": artifact["build_attempts"]}
    (case / "candidate.json").write_text(json.dumps({
        "candidate_id": cid, "skill": skill, "prompt": artifact["prompt"],
        "base_image": base_image, "containerfile": artifact["containerfile"],
        "built_image": artifact["built_image"], "sanity": artifact["sanity"],
        "provenance": candidate_provenance,
    }, indent=2))
    return str(case), {
        "validated": True,
        "attempts": artifact["build_attempts"],
    }, cost


def mark_tried(state, state_path, branch_key_, mutation):
    state["tried"].setdefault(branch_key_, []).append(mutation)
    atomic_write_json(state_path, state)


# ------------------------------------------------------------------ CLI

def main():
    ap = argparse.ArgumentParser(description="Extract guards + synthesize one validated test")
    ap.add_argument("--tree", required=True)
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--props", required=True)
    ap.add_argument("--out", required=True, help="dir for the synthesized case")
    ap.add_argument("--model", default="qwen3.6-flash")
    ap.add_argument("--extract-only", action="store_true",
                    help="only extract/refresh guards + print the frontier")
    args = ap.parse_args()

    tree = json.loads(pathlib.Path(args.tree).read_text())
    skill = tree.get("skill") or pathlib.Path(args.skill_dir).name
    props = json.loads(pathlib.Path(args.props).read_text())

    state, cost = extract_all_guards(tree, args.tree, args.model, skill=skill)
    _, state_path = load_guard_state(args.tree)
    frontier = build_frontier(state)
    print(f"branches: {len(state['guards'])}  (deferred non-E0: {len(state['deferred'])})")
    print(f"frontier: {len(frontier)} guard(s) with untried mutations")
    for it in frontier:
        print(f"  • {it['guard']['condition'][:80]}")
        for m in it["mutations"]:
            print(f"      untried: {m[:90]}")
    for k, g in state["guards"].items():
        for d in g.get("disagreements", []) or []:
            print(f"  !! outcome/reasoning DISAGREEMENT at {k}: {d.get('note','')[:120]}")
    if args.extract_only:
        return

    target, c = select_target(frontier, props, args.model, skill=skill)
    cost += c
    if not target:
        print("frontier empty — nothing to synthesize")
        return
    g = target["item"]["guard"]
    print(f"\nselected: {g['condition'][:80]}")
    print(f"  mutation: {target['mutation'][:100]}")
    print(f"  targets property: {target['targeted_property']}  — {target['rationale'][:140]}")

    case, info, c = synthesize(tree, target, skill, args.skill_dir, args.base,
                               args.model, args.out)
    cost += c
    mark_tried(state, state_path, g["branch_key"], target["mutation"])
    if case:
        print(f"\nVALIDATED candidate (no agent spent) -> {case}  (${cost:.4f})")
        print(f"  → run: python -m skillrace.run_case --case {case} "
              f"--skill-dir {args.skill_dir} --out runs/<name>")
    else:
        print(f"\nsynthesis FAILED after retries: {info.get('error')}  (${cost:.4f})")


if __name__ == "__main__":
    main()
