"""Simplified-trace renderer + episode-count target (deterministic, no model).

Turns a raw pi `session.jsonl` into a compact, human/LLM-readable `simplified_trace.txt`
that the segmenter agent reads to decide episode boundaries. It also computes the
TARGET number of episodes from the tool-call count.

Rendering rules (per the design discussion):
  - One block per assistant TURN: its reasoning (thinking), then its tool call(s).
  - Tool calls are numbered GLOBALLY (1..N) — these indices are what the segmenter
    references for episode spans, and a boundary may only fall at a turn start (a new
    reasoning point), so the edge between two episodes is a real piece of reasoning.
  - Each tool call shows its "big" field truncated to head+tail lines; the small field
    is shown verbatim. No duplication: a `write`'s content lives in args (result is a
    short status), a `read`/`bash`'s output lives in the result.

Target episodes: a SMOOTH, monotonic, saturating function of the tool-call count N —
  D = 3 + N/50 ; target = round(N / D).
Passes through divisor 3/4/5 at N≈0/50/100 (the tiered intuition) without the step
discontinuities, and saturates (target → ~50) so very long traces don't scale linearly.

Usage:
  python -m skillrace.simplify_trace --run runs/frontend-ceramicist \
      --out runs/frontend-ceramicist/simplified_trace.txt
"""
from __future__ import annotations
import argparse
import json
import pathlib

HEAD, TAIL = 15, 5  # truncate a long field to first HEAD + last TAIL lines


def target_episodes(n_tool_calls: int) -> int:
    """Smooth monotonic target: D = 3 + N/50, target = round(N/D), min 1."""
    if n_tool_calls <= 0:
        return 0
    d = 3.0 + n_tool_calls / 50.0
    return max(1, round(n_tool_calls / d))


def _truncate(text: str, head: int = HEAD, tail: int = TAIL) -> str:
    text = (text or "").rstrip("\n")
    if not text:
        return "(empty)"
    lines = text.splitlines()
    if len(lines) <= head + tail + 1:
        return "\n".join(lines)
    omitted = len(lines) - head - tail
    return "\n".join(lines[:head]
                     + [f"      … ({omitted} lines truncated for brevity) …"]
                     + lines[-tail:])


def _args_line(name: str, args: dict) -> tuple[str, str | None]:
    """Return (one_line_summary, big_block_or_None) for a tool call's ARGS."""
    a = args or {}
    if name == "bash":
        return ("$ " + (a.get("command", "")).split("\n")[0][:200],
                a.get("command") if "\n" in (a.get("command") or "") else None)
    if name == "read":
        return (a.get("path", ""), None)
    if name == "write":
        return (a.get("path", ""), a.get("content"))          # content is the big field
    if name == "edit":
        return (a.get("path", ""),
                f"- old:\n{a.get('oldText','')}\n+ new:\n{a.get('newText','')}")
    # fallback: compact json
    s = json.dumps(a)
    return (s[:200], s if len(s) > 200 else None)


def render(session_path: pathlib.Path) -> tuple[str, int]:
    """Render session.jsonl -> (simplified_text, n_tool_calls)."""
    rows = []
    for line in open(session_path):
        try:
            rows.append(json.loads(line))
        except Exception:
            continue

    # map toolCallId -> result text (+ error flag)
    results = {}
    for r in rows:
        m = r.get("message", {})
        if m.get("role") == "toolResult":
            txt = "".join(b.get("text", "") for b in m.get("content", []) if isinstance(b, dict))
            results[m.get("toolCallId")] = (txt, bool(m.get("isError")))

    # Flat sequence of globally-numbered tool calls. Reasoning is shown INLINE on the
    # first tool call of each assistant message (where a new `thinking` block begins) —
    # NOT as turn/episode-looking chunks, so the rendered trace is UNSEGMENTED and does
    # not telegraph where episodes are. A `reasoning:` line marks a candidate boundary;
    # the agent decides which of those actually start a new episode (and groups the rest).
    out, tc = [], 0
    for r in rows:
        m = r.get("message", {})
        if m.get("role") != "assistant":
            continue
        content = m.get("content", [])
        tool_calls = [b for b in content if b.get("type") == "toolCall"]
        if not tool_calls:
            continue  # a message with no tool call can't host an episode boundary
        reasoning = " ".join(b.get("thinking", "").strip()
                             for b in content if b.get("type") == "thinking").strip()
        note = " ".join(b.get("text", "").strip()
                        for b in content if b.get("type") == "text").strip()
        for j, b in enumerate(tool_calls):
            tc += 1
            name = b.get("name", "?")
            summary, big = _args_line(name, b.get("arguments", {}))
            res_txt, is_err = results.get(b.get("id"), ("(no result captured)", False))
            out.append(f"\nTool Call {tc} — {name}")
            if j == 0:  # the message's reasoning attaches to its FIRST tool call
                out.append("  reasoning: " + (_truncate(reasoning, 12, 3) if reasoning else "(none)"))
                if note:
                    out.append("  note: " + _truncate(note, 4, 1))
            out.append(f"  args: {summary}")
            if big:
                out.append("  args-body:\n" + _truncate(big))
            tag = "result(ERROR)" if is_err else "result"
            out.append(f"  {tag}:\n" + _truncate(res_txt))
    return "\n".join(out).lstrip("\n"), tc


def call_reasonings(session_path):
    """List where index i (0-based) is the verbatim reasoning of the assistant message
    that owns tool call i+1 — matches render()'s global tool-call numbering exactly.
    The assembler uses this to attach each episode's `opening_reasoning` (the edge)."""
    out = []
    for line in open(session_path):
        try:
            m = json.loads(line).get("message", {})
        except Exception:
            continue
        if m.get("role") != "assistant":
            continue
        content = m.get("content", [])
        tcs = [b for b in content if b.get("type") == "toolCall"]
        if not tcs:
            continue
        reasoning = " ".join(b.get("thinking", "").strip()
                             for b in content if b.get("type") == "thinking").strip()
        out.extend([reasoning] * len(tcs))
    return out


def main():
    ap = argparse.ArgumentParser(description="Render a simplified trace + episode target")
    ap.add_argument("--run", required=True, help="run dir (uses raw/session.jsonl)")
    ap.add_argument("--out", help="output path (default <run>/simplified_trace.txt)")
    args = ap.parse_args()

    run_dir = pathlib.Path(args.run)
    sess = run_dir / "raw" / "session.jsonl"
    manifest = json.loads((run_dir / "run.json").read_text())
    body, n = render(sess)
    target = target_episodes(n)
    header = (f"### SIMPLIFIED TRACE — run {manifest.get('run_id')} | skill {manifest.get('skill')}\n"
              f"### PROMPT: {manifest.get('prompt')}\n"
              f"### tool_calls={n}  target_episodes≈{target}  (D=3+N/50={3 + n/50.0:.2f})\n")
    text = header + "\n" + body + "\n"

    out = pathlib.Path(args.out) if args.out else run_dir / "simplified_trace.txt"
    out.write_text(text)
    print(f"wrote {out}  ({len(text)} chars)")
    print(f"  tool_calls={n}  target_episodes≈{target}")


if __name__ == "__main__":
    main()
