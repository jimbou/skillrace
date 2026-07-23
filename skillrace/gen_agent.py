"""Hybrid, SKILL-AGNOSTIC test generator.

WE control diversity (one direct Yunwu call proposes K natural-language test IDEAS,
with a digest so repeated runs stay distinct). The PI AGENT then realizes each idea:
it reads the skill, writes a (prompt, env Dockerfile FROM <base>), BUILDS it to confirm
it works, confirms the env is a genuine unsolved start, and saves the working ones.
We review the saved cases afterward.

The agent runs ON THE HOST (its `bash` reaches the Docker daemon to build), reads the
copied-in skill, and writes results to <out>/cases/.

Reports: which model actually ran (confirming the one we asked for), and total
in/out tokens + price for the whole process (proposer call + agent turns), priced
from our own table (the host models.json may zero some models).

Usage:
  python -m skillrace.gen_agent --skill-dir skills/fix-failing-test \
      --base skillrace/fix-failing-test:base --k 3 --model glm-5 --out out/genagent
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
from .generator import skill_context, propose_batch
from .model_policy import PROVIDER_CREDIT_RATES

AGENT_PROMPT = """\
You are realizing a set of {k} TEST CASES for a coding-agent skill. The skill is in \
./skill/ — read ./skill/SKILL.md for its purpose.

Here are {k} test IDEAS (each a task + an environment, in natural language):

{ideas}

For EACH idea N (1..{k}), produce a concrete test case = (prompt, env):
- prompt: the exact task to give the agent-under-test — faithful to idea N's task and \
the skill's purpose. (Make prompts genuinely reflect each idea; they need not be identical.)
- env: a Dockerfile that begins EXACTLY with `FROM {base}` and then ADDS the starting \
state idea N's environment describes. The base already provides the toolchain, git, and \
a /workspace project — build ON it; do NOT add a second FROM. Create whatever the \
scenario needs with `RUN cat > /workspace/<path> <<'EOF' ... EOF` heredocs, version \
pins, repo state, etc.

Steps for each idea N:
  1. `mkdir -p ./cases/case<N>`
  2. Write ./cases/case<N>/Dockerfile (starts with `FROM {base}`).
  3. Build it: `docker build -t skillrace-gen-case<N> ./cases/case<N>` — if it FAILS, \
read the error, fix the Dockerfile, rebuild until it builds.
  4. Confirm the env is a GENUINE, UNSOLVED starting point (e.g. run the project's \
tests and verify they actually FAIL / the target isn't already done); adjust if needed.
  5. Write ./cases/case<N>/candidate.json with exactly:
     {{"summary": "<=12 words", "prompt": "<the task>", "base_image": "{base}", "idea_index": <N>}}
  Keep ONLY cases that build. When all are done, print a short numbered summary.
"""


def _agent_tokens_and_model(trace_path):
    """Sum the agent's in/out tokens from the pi session trace; collect models used."""
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
    ap = argparse.ArgumentParser(description="Hybrid skill-agnostic test generator")
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--model", default="glm-4.5-flash", help="model for BOTH the proposer and the agent")
    ap.add_argument("--temperature", type=float, default=0.9, help="proposer temperature")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()
    if not os.environ.get("yunwu_key"):
        raise SystemExit("yunwu_key must be set")

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    skill_dst = out / "skill"
    if skill_dst.exists():
        shutil.rmtree(skill_dst)
    shutil.copytree(args.skill_dir, skill_dst)
    (out / "cases").mkdir(exist_ok=True)

    # --- step 1: WE propose K diverse NL ideas (controlled diversity) ---
    ctx = skill_context(pathlib.Path(args.skill_dir))
    print(f"proposing {args.k} ideas ({args.model}) ...")
    skill_name = pathlib.Path(args.skill_dir).name
    ideas, presp = propose_batch(ctx, [], args.k, args.model, args.temperature,
                                 reasoning=False, skill=skill_name)
    prop_in = presp["usage"].get("prompt_tokens", 0)
    prop_out = presp["usage"].get("completion_tokens", 0)
    (out / "ideas.json").write_text(json.dumps(ideas, indent=2))
    for i, it in enumerate(ideas, 1):
        print(f"  idea {i}: {it['summary']}")

    ideas_txt = "\n".join(
        f"{i}. summary: {it['summary']}\n   task: {it['task']}\n   env: {it['env']}"
        for i, it in enumerate(ideas, 1))
    prompt = AGENT_PROMPT.format(k=len(ideas), base=args.base, ideas=ideas_txt)

    # --- step 2: the AGENT realizes each idea (build + verify + save) ---
    trace = (out / "gen_trace.jsonl").resolve()
    cmd = ["pi", "--provider", "yunwu", "--model", args.model, "--print",
           "--tools", "bash,read,write,edit", "--session", str(trace), prompt]
    print(f"realizing with pi agent ({args.model}) ...")
    t0 = time.time()
    with open(out / "gen_stdout.txt", "w") as so, open(os.devnull) as dn:
        try:
            rc = subprocess.run(cmd, cwd=out, stdin=dn, stdout=so,
                                stderr=subprocess.STDOUT, timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:
            rc = 124
    dt = time.time() - t0

    # make each saved case self-describing so the SEPARATE runner has all it needs
    skill_name = pathlib.Path(args.skill_dir).name
    for cj in sorted((out / "cases").glob("*/candidate.json")):
        try:
            d = json.load(open(cj))
        except Exception:
            continue
        d.setdefault("skill", skill_name)
        d.setdefault("base_image", args.base)
        d["dockerfile"] = "Dockerfile"
        json.dump(d, open(cj, "w"), indent=2)

    # --- accounting ---
    agent_in, agent_out, models = _agent_tokens_and_model(trace)
    log_usage("generate.agent", args.model, agent_in, agent_out, skill_name)
    total_in, total_out = prop_in + agent_in, prop_out + agent_out
    pin, pout = PROVIDER_CREDIT_RATES.get(args.model, (0.0, 0.0))
    price = (total_in * pin + total_out * pout) / 1e6
    cases = sorted((out / "cases").glob("*/candidate.json"))

    print(f"\nagent rc={rc} in {dt:.1f}s; saved {len(cases)} case(s):")
    for c in cases:
        try:
            print(f"  - {c.parent.name}: {json.load(open(c)).get('summary')}")
        except Exception as e:
            print(f"  - {c.parent.name}: (bad candidate.json: {e})")
    print(f"\nmodel asked: {args.model!r}   model(s) actually used by agent: {models or '(trace missing)'}")
    if models and not any(args.model in m for m in models):
        print("  !! WARNING: agent used a different model than requested")
    print(f"tokens: proposer in/out={prop_in}/{prop_out}  agent in/out={agent_in}/{agent_out}")
    print(f"TOTAL in={total_in}  out={total_out}  cost=⚡{price:.4f} "
          f"(priced at {args.model} = ⚡{pin}/{pout} per 1M in/out)")
    (out / "accounting.json").write_text(json.dumps(
        {"model": args.model, "models_used": sorted(models), "seconds": round(dt, 1),
         "proposer": {"in": prop_in, "out": prop_out},
         "agent": {"in": agent_in, "out": agent_out},
         "total": {"in": total_in, "out": total_out, "price_provider_credits": round(price, 6)},
         "cases": len(cases)}, indent=2))


if __name__ == "__main__":
    main()
