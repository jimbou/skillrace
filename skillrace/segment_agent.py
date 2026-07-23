"""Episode Segmenter — the AGENT version (the agreed design).

Flow:
  1. DETERMINISTIC script renders the run's session.jsonl -> simplified_trace.txt
     (+ a smooth episode-count target).  [skillrace/simplify_trace.py]
  2. A pi AGENT is given the baked-in few-shot example (input + correct split) and the
     simplified_trace.txt, and is asked to WRITE BACK the split as JSON
     (episodes.raw.json). The agent can read/page the trace file itself, so long traces
     are fine. No Docker, no container — trace-only, cheap.
  3. DETERMINISTIC assembler validates the spans (partition, in-range), attaches each
     episode's `opening_reasoning` verbatim from the trace, and writes episodes.json.

This mirrors gen_agent.py (an agent does the fuzzy work and writes a JSON artifact;
our code validates + assembles). See docs/design/episode-segmenter.md.

Usage:
  python -m skillrace.segment_agent --run runs/mcp-case1 --model glm-4.5-flash
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import time

from .closeai import log_usage
from .model_policy import PROVIDER_CREDIT_RATES
from .simplify_trace import render, target_episodes, call_reasonings
from .segment import validate, assemble  # reuse the deterministic checks

FEWSHOT = pathlib.Path(__file__).parent / "fewshot"

AGENT_PROMPT = """\
You split ONE coding-agent run into EPISODES.

STEP 1 — study the worked example in this directory:
  - read `example_input.txt`  (a simplified trace, same format you must segment)
  - read `example_output.json` (the CORRECT split of that trace into episodes)

STEP 2 — read `simplified_trace.txt`. This is the trace you must split. It is a FLAT,
globally-numbered list of the agent's tool calls, with the agent's `reasoning:` shown
inline wherever its thinking shifts. Its header states a target episode count.

RULES (same as the example):
  - An EPISODE is a contiguous run of tool calls pursuing ONE sub-goal.
  - Start a new episode where the reasoning makes a CONTINGENT, task-specific DECISION —
    a choice that could have gone otherwise (what to build, a diagnosis, a fix, handling
    what the environment does or doesn't already provide) — NOT at every generic phase.
  - Group consecutive tool calls AND consecutive reasoning shifts that serve the same
    sub-goal into one episode. A genuinely low-decision stretch (e.g. bulk reading the
    same kind of file) can be one episode — BUT a long investigation still SPLITS at
    PIVOTS and DISCOVERIES: a change in WHAT is being investigated, a decision to change
    approach (e.g. "no server-side examples found, switch to reading the type
    definitions"), or a finding that changes the plan each starts a new episode. A
    20-or-30-call block is almost never a single episode.
  - Aim for roughly the target number of episodes. The target is soft, but producing FAR
    FEWER than the target usually means you UNDER-SPLIT: when your count is well below the
    target, look INSIDE your longest episodes for pivots, distinct sub-targets, and
    plan-changing discoveries, and split them. A boundary may only fall at a tool call
    that has a `reasoning:` line.
  - For each episode give: `intent` (sub-goal), `what_it_did` (actions, one line), and
    `outcome` — read ONLY from the tool RESULTS shown (exit codes, printed text, errors),
    NEVER from the agent's reasoning/claims.
  - The episodes must PARTITION every tool call in order, with NO gaps or overlaps
    (episode 1 starts at call 1; the last ends at the final call).

STEP 3 — write your answer to `./episodes.raw.json` (and ONLY that file), in exactly:
  {"episodes":[{"start_call":N,"end_call":N,"intent":"...","what_it_did":"...","outcome":"..."}, ...]}
Then stop.
"""


def _agent_tokens(trace_path):
    tin = tout = 0
    models = set()
    if not pathlib.Path(trace_path).exists():
        return tin, tout, models
    for line in open(trace_path):
        try:
            m = json.loads(line).get("message", {})
        except Exception:
            continue
        if m.get("role") == "assistant":
            u = m.get("usage") or {}
            tin += u.get("input", 0) or 0
            tout += u.get("output", 0) or 0
            if m.get("model"):
                models.add(m["model"])
    return tin, tout, models


def main():
    ap = argparse.ArgumentParser(description="Segment a run into episodes via a pi agent")
    ap.add_argument("--run", required=True, help="run dir (uses raw/session.jsonl)")
    ap.add_argument("--model", default="glm-4.5-flash")
    ap.add_argument("--out", help="output path (default <run>/episodes.json)")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    if not os.environ.get("yunwu_key"):
        raise SystemExit("yunwu_key must be set")

    run_dir = pathlib.Path(args.run)
    sess = run_dir / "raw" / "session.jsonl"

    # 1) DETERMINISTIC: render the simplified trace + target, and the per-call reasonings.
    simplified, n = render(sess)
    target = target_episodes(n)
    reasonings = call_reasonings(sess)
    print(f"tool_calls={n}  target_episodes≈{target}")

    # working dir for the agent: simplified trace + the few-shot example
    work = run_dir / "seg_agent"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / "simplified_trace.txt").write_text(simplified)
    shutil.copy(FEWSHOT / "segmenter_example_input.txt", work / "example_input.txt")
    shutil.copy(FEWSHOT / "segmenter_example_output.json", work / "example_output.json")

    # 2) the AGENT splits it and writes episodes.raw.json
    trace = (work / "seg_trace.jsonl").resolve()
    cmd = ["pi", "--provider", "yunwu", "--model", args.model, "--print",
           "--tools", "bash,read,write", "--session", str(trace), AGENT_PROMPT]
    print(f"running segmenter agent ({args.model}) ...")
    t0 = time.time()
    with open(work / "seg_stdout.txt", "w") as so, open(os.devnull) as dn:
        try:
            rc = subprocess.run(cmd, cwd=work, stdin=dn, stdout=so,
                                stderr=subprocess.STDOUT, timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:
            rc = 124
    dt = time.time() - t0

    raw_path = work / "episodes.raw.json"
    out = pathlib.Path(args.out) if args.out else run_dir / "episodes.json"
    if not raw_path.exists():
        out.write_text(json.dumps({"unsegmentable": True, "error": "agent wrote no episodes.raw.json",
                                   "rc": rc}, indent=2))
        raise SystemExit(f"agent produced no episodes.raw.json (rc={rc}); see {work}/seg_stdout.txt")

    # 3) DETERMINISTIC: validate spans + attach opening_reasoning
    raw = json.loads(raw_path.read_text())
    eps = raw["episodes"] if isinstance(raw, dict) else raw
    ok, err = validate(eps, n)

    ain, aout, models = _agent_tokens(trace)
    log_usage("segment.agent", args.model, ain, aout, None)
    pin, pout = PROVIDER_CREDIT_RATES.get(args.model, (0.0, 0.0))
    price = (ain * pin + aout * pout) / 1e6

    if not ok:
        out.write_text(json.dumps({"unsegmentable": True, "error": err, "raw": eps}, indent=2))
        raise SystemExit(f"UNSEGMENTABLE: {err}  (agent rc={rc}, ⚡{price:.4f}); wrote {out}")

    episodes = assemble(eps, reasonings)
    out.write_text(json.dumps({"run": str(run_dir), "n_tool_calls": n,
                               "target_episodes": target, "model": args.model,
                               "episodes": episodes}, indent=2))
    print(f"\nagent rc={rc} in {dt:.1f}s, ⚡{price:.4f} (model(s) used: {models or '?'})")
    print(f"segmented into {len(episodes)} episodes -> {out}\n")
    for e in episodes:
        print(f"  Ep{e['index']} [calls {e['start_call']}-{e['end_call']}] {e['intent']}")
        print(f"        outcome: {e['outcome'][:110]}")


if __name__ == "__main__":
    main()
