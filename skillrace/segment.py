"""Episode Segmenter — v1 (single direct model call).

Reads a run's trace, renders the simplified trace, asks the judgment model (DIRECT to
Yunwu, temp 0) to split it into episodes-with-summaries using the baked-in few-shot
example + the smooth target, validates the spans deterministically, attaches each
episode's `opening_reasoning` verbatim from the trace, and writes episodes.json.

This is the single-call version — good for traces that fit one context window. The
paging *agent* variant (for very long traces) is future work; see
docs/design/episode-segmenter.md. The deterministic renderer/target/assembler here are
exactly as specified there.

Usage:
  python -m skillrace.segment --run runs/mcp-case1 --out runs/mcp-case1/episodes.json
"""
from __future__ import annotations
import argparse
import json
import pathlib

from .closeai import chat, extract_json
from .simplify_trace import render, target_episodes, call_reasonings

FEWSHOT = pathlib.Path(__file__).parent / "fewshot"

SEG_SYS = (
    "You split one coding-agent run into EPISODES. You are given a FLAT, globally-numbered "
    "list of the agent's tool calls, with the agent's `reasoning:` shown inline wherever its "
    "thinking shifts.\n"
    "An EPISODE is a contiguous run of tool calls pursuing ONE sub-goal. Start a new episode "
    "where the reasoning makes a CONTINGENT DECISION specific to this task — a choice that "
    "could have gone otherwise (what to build, a diagnosis, a fix, handling what the "
    "environment does or doesn't already provide) — NOT at every generic phase. Group "
    "consecutive tool calls AND consecutive reasoning shifts that serve the same sub-goal "
    "into one episode. A genuinely low-decision stretch (bulk reading) can be one episode, "
    "BUT a long investigation still SPLITS at PIVOTS and DISCOVERIES — a change in WHAT is "
    "being investigated, a decision to change approach, or a finding that changes the plan "
    "each starts a new episode (a 20-30 call block is almost never one episode). Aim for "
    "roughly the TARGET number; producing FAR FEWER than the target usually means you "
    "UNDER-SPLIT, so look inside your longest episodes for pivots and split them. A "
    "boundary may only fall at a tool call that has a `reasoning:` line.\n"
    "For each episode write: `intent` (the sub-goal, a few words), `what_it_did` (the "
    "actions, one line), and `outcome` — the RESULT, read ONLY from the tool RESULTS shown "
    "(exit codes, printed text, errors, status), NEVER from the agent's reasoning/claims.\n"
    "The episodes must PARTITION every tool call in order, with NO gaps or overlaps "
    "(episode 1 starts at call 1; the last ends at the final call).\n"
    "Return ONLY JSON: {\"episodes\":[{\"start_call\":N,\"end_call\":N,\"intent\":\"...\","
    "\"what_it_did\":\"...\",\"outcome\":\"...\"}, ...]}."
)


def segment_text(simplified_text, target, model):
    ex_in = (FEWSHOT / "segmenter_example_input.txt").read_text()
    ex_out = (FEWSHOT / "segmenter_example_output.json").read_text()
    user = (
        "Here is a WORKED EXAMPLE — an input trace and the correct split.\n\n"
        f"===== EXAMPLE INPUT =====\n{ex_in}\n\n"
        f"===== EXAMPLE OUTPUT =====\n{ex_out}\n\n"
        f"Now segment the trace below. Aim for ~{target} episodes (a soft target — let the "
        f"decisions decide).\n\n===== TRACE TO SEGMENT =====\n{simplified_text}\n\n"
        "Return ONLY the JSON object."
    )
    resp = chat([{"role": "system", "content": SEG_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, reasoning=False, max_tokens=3000,
                tag="segment", skill=None)
    obj = extract_json(resp["content"])
    eps = obj["episodes"] if isinstance(obj, dict) else obj
    return eps, resp["cost_provider_credits"]


def validate(eps, n):
    """Spans must partition 1..n in order, no gaps/overlaps. Returns (ok, error)."""
    if not eps:
        return False, "no episodes"
    prev = 0
    for i, e in enumerate(eps):
        s, en = e.get("start_call"), e.get("end_call")
        if not isinstance(s, int) or not isinstance(en, int):
            return False, f"episode {i}: non-integer span"
        if s != prev + 1:
            return False, f"episode {i}: start_call {s} should be {prev + 1} (gap/overlap)"
        if en < s:
            return False, f"episode {i}: end_call {en} < start_call {s}"
        if not (e.get("intent") and e.get("outcome")):
            return False, f"episode {i}: missing intent/outcome"
        prev = en
    if prev != n:
        return False, f"last episode ends at {prev}, expected {n}"
    return True, ""


def assemble(eps, reasonings):
    """Attach index + verbatim opening_reasoning (the edge into each episode)."""
    out = []
    for i, e in enumerate(eps):
        sc = e["start_call"]
        opening = reasonings[sc - 1] if 1 <= sc <= len(reasonings) else ""
        out.append({"index": i + 1, "start_call": sc, "end_call": e["end_call"],
                    "intent": e.get("intent", ""), "what_it_did": e.get("what_it_did", ""),
                    "outcome": e.get("outcome", ""), "opening_reasoning": opening})
    return out


def main():
    ap = argparse.ArgumentParser(description="Segment a run into episodes (v1 single-call)")
    ap.add_argument("--run", required=True, help="run dir (uses raw/session.jsonl)")
    ap.add_argument("--model", default="glm-4.5-flash")
    ap.add_argument("--out", help="output path (default <run>/episodes.json)")
    ap.add_argument("--max-repair", type=int, default=1, help="re-segment attempts on invalid spans")
    args = ap.parse_args()

    run_dir = pathlib.Path(args.run)
    sess = run_dir / "raw" / "session.jsonl"
    simplified, n = render(sess)
    target = target_episodes(n)
    reasonings = call_reasonings(sess)
    print(f"tool_calls={n}  target_episodes≈{target}")

    eps, cost = segment_text(simplified, target, args.model)
    ok, err = validate(eps, n)
    attempts = 0
    while not ok and attempts < args.max_repair:
        attempts += 1
        print(f"  [invalid split] {err} — re-segmenting ({attempts})")
        eps, c = segment_text(simplified + f"\n\n(Your previous split was invalid: {err}. "
                              "Make the spans partition all tool calls in order.)",
                              target, args.model)
        cost += c
        ok, err = validate(eps, n)

    out = pathlib.Path(args.out) if args.out else run_dir / "episodes.json"
    if not ok:
        out.write_text(json.dumps({"unsegmentable": True, "error": err, "raw": eps}, indent=2))
        print(f"UNSEGMENTABLE: {err}  (wrote {out})")
        return

    episodes = assemble(eps, reasonings)
    out.write_text(json.dumps({"run": str(run_dir), "n_tool_calls": n,
                               "target_episodes": target, "episodes": episodes}, indent=2))
    print(f"\nsegmented into {len(episodes)} episodes (cost ⚡{cost:.4f}) -> {out}\n")
    for e in episodes:
        print(f"  Ep{e['index']} [calls {e['start_call']}-{e['end_call']}] {e['intent']}")
        print(f"        outcome: {e['outcome'][:110]}")


if __name__ == "__main__":
    main()
