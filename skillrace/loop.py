"""The assembled campaign loop — one loop, three drop-in generators.

  seed phase   : N seed cases from the SHARED seed generator (identical across
                 methods), each compiled (pre-run checks), run, checked, folded.
  explore phase: until the agent-run budget is spent —
                 propose -> compile checks -> run agent -> check properties -> fold.

Only `make_generator(method)` differs between rungs ("random" | "greybox" |
"skillrace"); the runner and the property checker are byte-identical subprocess
invocations, so a measured difference is about TEST GENERATION, not detection.

Per-iteration record (campaign.json): candidate + provenance, run dir, termination,
verdict summary, violated property ids, wall-clock, costs, and — for the skillrace
rung — the run classification against the targeted branch:
  predicted_divergence  it reached the branch and took a NEW way (coverage gained)
  no_divergence         it reached the branch but behaved as before (guard not causal)
  path_miss             it never reached the branch (an earlier guard failed)

Usage:
  python -m skillrace.loop --method skillrace --skill fix-failing-test \
      --skill-dir skills/fix-failing-test --base skillrace/fix-failing-test:base \
      --props skills/fix-failing-test/properties.json \
      --budget 20 --seed-count 6 --out out/campaign/skillrace/fix-failing-test
"""
from __future__ import annotations
import argparse
import json
import pathlib
import subprocess
import sys
import time

from .generator import RandomGenerator
from .greybox import GreyboxGenerator
from .compile_checks import compile_case
from .simplify_trace import render, target_episodes, call_reasonings
from .segment import segment_text, validate as validate_spans, assemble
from .tree import fold as tree_fold, empty_tree
from . import guards as G


# ------------------------------------------------------------------ plumbing

def materialize_case(cand, cases_dir):
    """Write a candidate dict as a runnable case dir (Dockerfile + candidate.json)."""
    case = pathlib.Path(cases_dir) / cand["candidate_id"]
    case.mkdir(parents=True, exist_ok=True)
    (case / "Dockerfile").write_text(cand["containerfile"])
    (case / "candidate.json").write_text(json.dumps(cand, indent=2))
    return str(case)


def run_agent(case_dir, run_dir, agent_model, wall_clock):
    """SHARED runner (byte-identical across rungs)."""
    p = subprocess.run([sys.executable, "-m", "skillrace.run_case",
                        "--case", str(case_dir), "--model", agent_model,
                        "--out", str(run_dir), "--wall-clock", str(wall_clock)],
                       capture_output=True, text=True)
    tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-4:])
    return p.returncode == 0, tail


def check_run(run_dir, model):
    """SHARED property checker (precompiled per-case checks + fixed core)."""
    p = subprocess.run([sys.executable, "-m", "skillrace.check_properties",
                        "--run", str(run_dir), "--model", model],
                       capture_output=True, text=True)
    vp = pathlib.Path(run_dir) / "verdicts.json"
    verdicts = json.loads(vp.read_text()) if vp.exists() else []
    return verdicts, (p.stdout + p.stderr).strip().splitlines()[-3:]


def segment_and_fold(run_dir, tree_path, model, skill):
    """SkillRACE fold: segment the run, fold its episode line into the tree.
    Returns (actions|None, error, cost)."""
    run_dir = pathlib.Path(run_dir)
    sess = run_dir / "raw" / "session.jsonl"
    if not sess.exists():
        return None, "no session trace", 0.0
    simplified, n = render(sess)
    if n == 0:
        return None, "empty trace", 0.0
    eps, cost = segment_text(simplified, target_episodes(n), model)
    ok, err = validate_spans(eps, n)
    if not ok:
        eps, c = segment_text(simplified + f"\n\n(Your previous split was invalid: {err}. "
                              "Make the spans partition all tool calls in order.)",
                              target_episodes(n), model)
        cost += c
        ok, err = validate_spans(eps, n)
    if not ok:
        (run_dir / "episodes.json").write_text(json.dumps(
            {"unsegmentable": True, "error": err}, indent=2))
        return None, f"unsegmentable: {err}", cost
    episodes = assemble(eps, call_reasonings(sess))
    (run_dir / "episodes.json").write_text(json.dumps(
        {"run": str(run_dir), "n_tool_calls": n, "episodes": episodes}, indent=2))

    tree_path = pathlib.Path(tree_path)
    tree = (json.loads(tree_path.read_text()) if tree_path.exists()
            else empty_tree(skill))
    cache_path = tree_path.with_suffix(".cache.json")
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    actions = tree_fold(tree, episodes, run_dir.name, model, cache,
                        run_meta={"dir": str(run_dir), "session": str(sess),
                                  "episodes": str(run_dir / "episodes.json")})
    tree_path.parent.mkdir(parents=True, exist_ok=True)
    tree_path.write_text(json.dumps(tree, indent=2))
    cache_path.write_text(json.dumps(cache, indent=2))
    return actions, None, cost


def classify(actions, parent_id):
    """How the new run relates to the branch it targeted (skillrace rung only)."""
    if actions is None:
        return "unfolded"
    ids = [nid for _, nid, _ in actions]
    if parent_id is None:                       # branch at the virtual root
        return ("predicted_divergence" if actions and actions[0][0] == "new"
                else "no_divergence")
    if parent_id not in ids:
        return "path_miss"
    i = ids.index(parent_id)
    if i + 1 >= len(actions):
        return "path_miss"                      # run ENDED at the branch node
    return "predicted_divergence" if actions[i + 1][0] == "new" else "no_divergence"


# ------------------------------------------------------------------ generators

class SkillRACEGenerator:
    """Components 2-5 behind the Generator protocol. propose() only ever returns a
    VALIDATED candidate case (or falls back to the seed generator, counted)."""

    def __init__(self, skill, skill_dir, base_image, props, model, out_dir,
                 seed_gen):
        self.skill, self.skill_dir, self.base = skill, skill_dir, base_image
        self.props, self.model = props, model
        self.out = pathlib.Path(out_dir)
        self.tree_path = self.out / "tree.json"
        self.seed_gen = seed_gen
        self.cost_usd = 0.0
        self.stats = {"synthesized": 0, "fallbacks": 0, "synth_failures": 0}
        self.last_target_parent = None      # parent node id of the targeted branch

    def propose(self, cases_dir):
        self.last_target_parent = None
        if self.tree_path.exists():
            tree = json.loads(self.tree_path.read_text())
            state, c = G.extract_all_guards(tree, self.tree_path, self.model,
                                            skill=self.skill)
            self.cost_usd += c
            frontier = G.build_frontier(state)
            target, c = G.select_target(frontier, self.props, self.model,
                                        skill=self.skill)
            self.cost_usd += c
            if target:
                case, info, c = G.synthesize(tree, target, self.skill,
                                             self.skill_dir, self.base, self.model,
                                             cases_dir)
                self.cost_usd += c
                st, sp = G.load_guard_state(self.tree_path)
                G.mark_tried(st, sp, target["item"]["guard"]["branch_key"],
                             target["mutation"])
                if case:
                    self.stats["synthesized"] += 1
                    self.last_target_parent = target["item"]["guard"]["parent_id"]
                    return case, "skillrace"
                self.stats["synth_failures"] += 1
        # frontier empty / synthesis failed -> diverse seed input (counted)
        self.stats["fallbacks"] += 1
        cand = self.seed_gen.propose()
        if cand is None:
            return None, None
        cand["provenance"]["source"] = "skillrace-fallback"
        return materialize_case(cand, cases_dir), "skillrace-fallback"

    def fold(self, case_dir, run_dir):
        actions, err, c = segment_and_fold(run_dir, self.tree_path, self.model,
                                           self.skill)
        self.cost_usd += c
        if err:
            print(f"  [fold] {err}")
        return actions

    def state(self):
        return {"skill": self.skill, "source": "skillrace", "stats": self.stats,
                "gen_cost_usd": round(self.cost_usd + self.seed_gen.cost_usd, 6)}


# ------------------------------------------------------------------ the loop

def regrade(case_dir, violated_props, k, runs_dir, i, model, agent_model, wall_clock):
    """Reproducibility regrade (tex §6): re-run the SAME validated case k times and
    re-check. Returns {prop_id: reproduced_count} out of k. 3/3 = genuine bug; a lower
    count = brittleness. Uses the shared runner + checker (expensive; opt-in)."""
    repro = {p: 0 for p in violated_props}
    for j in range(k):
        rd = runs_dir / f"r{i:03d}-regrade{j}"
        run_agent(case_dir, rd, agent_model, wall_clock)
        verdicts, _ = check_run(rd, model)
        viol = {v["property_id"] for v in verdicts if v.get("violated")}
        for p in violated_props:
            if p in viol:
                repro[p] += 1
    return repro


def run_campaign(method, skill, skill_dir, base, props_path, budget, seed_count,
                 out_dir, model="qwen3.6-flash", agent_model="qwen3.6-flash",
                 wall_clock=1800, greybox_level="L1", seed_k=5, seed_temp=0.9,
                 regrade_k=0):
    out = pathlib.Path(out_dir)
    cases_dir = out / "cases"
    runs_dir = out / "runs"
    cases_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    props = json.loads(pathlib.Path(props_path).read_text())

    seed_gen = RandomGenerator(skill, skill_dir, base, model=model, k=seed_k,
                               temperature=seed_temp, source="seed",
                               outdir=str(cases_dir))
    if method == "random":
        gen = RandomGenerator(skill, skill_dir, base, model=model, k=seed_k,
                              temperature=seed_temp, source="random",
                              outdir=str(cases_dir))
    elif method == "greybox":
        gen = GreyboxGenerator(skill, skill_dir, base, model=model,
                               level=greybox_level, temperature=seed_temp)
    elif method == "skillrace":
        gen = SkillRACEGenerator(skill, skill_dir, base, props, model, out, seed_gen)
    else:
        raise SystemExit(f"unknown method {method!r}")

    campaign = {"method": method, "skill": skill, "budget": budget,
                "seed_count": seed_count, "agent_model": agent_model,
                "greybox_level": greybox_level if method == "greybox" else None,
                "iterations": []}
    camp_path = out / "campaign.json"

    def one_iteration(i, case_dir, source):
        rec = {"i": i, "case": str(case_dir), "source": source}
        cand = json.loads((pathlib.Path(case_dir) / "candidate.json").read_text())
        rec["candidate_id"] = cand.get("candidate_id")
        rec["provenance"] = cand.get("provenance")
        t0 = time.time()
        try:
            _, cost = compile_case(case_dir, props, model,
                                   image=cand.get("built_image"))
            rec["compile_cost_usd"] = round(cost, 6)
        except Exception as e:
            rec["compile_error"] = str(e)[:300]
        run_dir = runs_dir / f"r{i:03d}-{cand.get('candidate_id', 'x')[:17]}"
        ok, tail = run_agent(case_dir, run_dir, agent_model, wall_clock)
        rec["run"] = str(run_dir)
        rj = run_dir / "run.json"
        if rj.exists():
            man = json.loads(rj.read_text())
            rec["termination"] = man.get("termination")
        else:
            rec["runner_error"] = tail[-300:]
        verdicts, _ = check_run(run_dir, model)
        rec["violated"] = [v["property_id"] for v in verdicts if v.get("violated")]
        rec["inconclusive"] = [v["property_id"] for v in verdicts
                               if v.get("holds") is None]
        rec["n_verdicts"] = len(verdicts)
        # optional reproducibility regrade of any violation (opt-in; re-runs the agent)
        if regrade_k > 0 and rec["violated"]:
            repro = regrade(case_dir, rec["violated"], regrade_k, runs_dir, i,
                            model, agent_model, wall_clock)
            rec["regrade"] = {"k": regrade_k, "reproduced": repro}
            rec["reproducible"] = [p for p, c in repro.items() if c == regrade_k]
        # every rung folds through ITS OWN generator (greybox: novelty; random: no-op)
        actions = gen.fold(cand, run_dir) if method != "skillrace" else \
            gen.fold(case_dir, run_dir)
        if method == "skillrace":
            rec["classification"] = classify(actions, gen.last_target_parent) \
                if source == "skillrace" else None
        rec["seconds"] = round(time.time() - t0, 1)
        campaign["iterations"].append(rec)
        camp_path.write_text(json.dumps(campaign, indent=2))
        flag = " ".join(rec["violated"]) or "-"
        print(f"[{i}] {source} {cand.get('candidate_id')} "
              f"violated: {flag}  ({rec['seconds']}s)")

    i = 0
    # --- seed phase (identical generator + params for every method) ---
    while i < min(seed_count, budget):
        cand = seed_gen.propose()
        if cand is None:
            break
        case_dir = materialize_case(cand, cases_dir)
        # every method folds its seed runs through its own generator (greybox
        # bootstraps its novelty corpus; skillrace its tree; random: no-op)
        one_iteration(i, case_dir, "seed")
        i += 1
    # --- exploration phase ---
    while i < budget:
        if method == "skillrace":
            case_dir, source = gen.propose(cases_dir)
        else:
            cand = gen.propose()
            case_dir = materialize_case(cand, cases_dir) if cand else None
            source = method
        if case_dir is None:
            print("generator exhausted")
            break
        one_iteration(i, case_dir, source)
        i += 1

    campaign["generator_state"] = gen.state()
    campaign["totals"] = {
        "runs": len(campaign["iterations"]),
        "distinct_violated_properties": sorted({p for r in campaign["iterations"]
                                                for p in r.get("violated", [])}),
        "runs_with_violation": sum(1 for r in campaign["iterations"]
                                   if r.get("violated")),
    }
    camp_path.write_text(json.dumps(campaign, indent=2))
    print(f"\ncampaign done: {campaign['totals']}")
    print(f"wrote {camp_path}")
    return campaign


def main():
    ap = argparse.ArgumentParser(description="Run one testing campaign (one method, one skill)")
    ap.add_argument("--method", required=True, choices=["random", "greybox", "skillrace"])
    ap.add_argument("--skill", required=True)
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--props", required=True)
    ap.add_argument("--budget", type=int, default=20, help="total agent runs")
    ap.add_argument("--seed-count", type=int, default=6)
    ap.add_argument("--model", default="qwen3.6-flash", help="judgment/generation model")
    ap.add_argument("--agent-model", default="qwen3.6-flash", help="agent under test")
    ap.add_argument("--wall-clock", type=int, default=1800)
    ap.add_argument("--greybox-level", default="L1", choices=["L0", "L1", "L2"])
    ap.add_argument("--seed-k", type=int, default=5,
                    help="ideas per proposer batch (smaller = less build-barrier latency)")
    ap.add_argument("--regrade-k", type=int, default=0,
                    help="on a violation, re-run the case this many times to grade "
                         "reproducibility (0 = off; tex uses 3)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    run_campaign(args.method, args.skill, args.skill_dir, args.base, args.props,
                 args.budget, args.seed_count, args.out, model=args.model,
                 agent_model=args.agent_model, wall_clock=args.wall_clock,
                 greybox_level=args.greybox_level, seed_k=args.seed_k,
                 regrade_k=args.regrade_k)


if __name__ == "__main__":
    main()
