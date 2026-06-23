"""Runner for a generated test case — a SEPARATE command from the generator.

The generator writes cases (Dockerfile + candidate.json) to a dir and stops. This
command takes ONE saved case and does the actual run: build the env image, run the
AGENT UNDER TEST (the skill, baked into the base image) on the case's prompt, and
capture the trace + cost + a workspace diff into a run dir.

Decoupled by design: generate whenever; run any case later, on demand, with the
inputs you choose (which case, which agent model, where to put the run).

Usage:
  python -m skillrace.run_case --case out/genagent/cases/case2 \
      --model qwen3.6-flash --out runs/ftt-case2
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import subprocess
import time
import uuid

from .closeai import PRICES, log_usage


def _docker_build(case_dir, tag):
    p = subprocess.run(["docker", "build", "-q", "-t", tag, str(case_dir)],
                       capture_output=True, text=True)
    return p.returncode == 0, (p.stderr or p.stdout)


def _trace_cost(trace_path, model):
    """Sum agent in/out tokens from the pi session trace; price via our table."""
    tin = tout = turns = 0
    if pathlib.Path(trace_path).exists():
        for line in open(trace_path):
            try:
                m = json.loads(line).get("message", {})
            except Exception:
                continue
            if m.get("role") == "assistant":
                turns += 1
                u = m.get("usage") or {}
                tin += u.get("input", 0) or 0
                tout += u.get("output", 0) or 0
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return {"model": model, "turns": turns, "in": tin, "out": tout,
            "price_usd": round((tin * pin + tout * pout) / 1e6, 6)}


def main():
    ap = argparse.ArgumentParser(description="Run one generated test case (agent under test)")
    ap.add_argument("--case", required=True, help="case dir (Dockerfile + candidate.json)")
    ap.add_argument("--model", default="qwen3.6-flash", help="agent-under-test model")
    ap.add_argument("--out", required=True, help="run output dir")
    ap.add_argument("--wall-clock", type=int, default=1800,
                    help="hard timeout for the agent run (default 30 min; design-iteration "
                         "skills like frontend-design iterate a lot)")
    ap.add_argument("--cleanup-grace", type=int, default=1800,
                    help="seconds after the run before a detached timebomb force-removes "
                         "the left-alive container (+ env image) if the checker hasn't")
    args = ap.parse_args()
    if not os.environ.get("CLOSE_API_KEY"):
        raise SystemExit("CLOSE_API_KEY must be set")

    case = pathlib.Path(args.case)
    cand = json.loads((case / "candidate.json").read_text())
    skill = cand.get("skill") or cand["base_image"].split("/")[-1].split(":")[0]
    prompt = cand["prompt"]

    out = pathlib.Path(args.out)
    (out / "logs").mkdir(parents=True, exist_ok=True)
    logs = (out / "logs").resolve()
    run_id = "run-" + uuid.uuid4().hex[:12]
    env_tag = f"skillrace/runenv-{run_id}"

    # 1) build the env image from the case's Dockerfile
    print(f"building env image from {case}/Dockerfile ...")
    ok, berr = _docker_build(case, env_tag)
    if not ok:
        raise SystemExit(f"env build failed:\n{berr[-1500:]}")

    # 2) run the agent under test (skill is baked into the base at /skills/<skill>).
    #    capture the session trace + a post-run workspace diff. --rm + --name so a
    #    timeout can tear the container down.
    # commit the post-setup state as the baseline, so the post-agent diff shows
    # exactly the agent's changes (incl. files it creates), regardless of how the
    # case Dockerfile was built.
    inner = ('cd /workspace && git add -A && '
             'git commit -q -m "skillrace: pre-agent baseline" || true; '
             f'pi --provider closeai --model {args.model} --print '
             f'--session /logs/session.jsonl --skill /skills/{skill} "$PI_PROMPT" </dev/null; '
             'cd /workspace && git add -A && '
             'git diff --cached HEAD > /logs/workspace.diff 2>/dev/null || true')
    # Start a LONG-LIVED container (sleep infinity), run the agent via `docker exec`,
    # and LEAVE the container running. The Property Checker runs its state checks in
    # this same live container (most faithful — not a fresh re-run of a commit) and
    # destroys it afterward. No `docker commit`.
    subprocess.run(["docker", "run", "-d", "--name", run_id, "--network=host",
                    "-e", "CLOSE_API_KEY", "-e", f"PI_PROMPT={prompt}",
                    "-v", f"{logs}:/logs", env_tag, "sleep", "infinity"],
                   check=True, capture_output=True)
    print(f"running agent under test ({args.model}) on skill {skill!r} ...")
    t0 = time.time()
    termination, container_alive = "completed", True
    try:
        p = subprocess.run(["docker", "exec", run_id, "sh", "-c", inner],
                           capture_output=True, text=True, timeout=args.wall_clock)
        rc, stdout = p.returncode, p.stdout
        termination = "completed" if rc == 0 else "error"
    except subprocess.TimeoutExpired:
        rc, stdout, termination = 124, "", "timeout"
        subprocess.run(["docker", "rm", "-f", run_id], capture_output=True)
        container_alive = False
    dt = time.time() - t0

    # Leave a TIMEBOMB: a detached process that force-removes the left-alive container
    # (+ env image) after --cleanup-grace seconds, unless the Property Checker removed
    # it first. Survives this process exiting (start_new_session). The checker normally
    # cleans up promptly, making the timebomb's `docker rm -f` a harmless no-op.
    if container_alive:
        bomb = (f"sleep {args.cleanup_grace}; "
                f"docker rm -f {run_id} >/dev/null 2>&1; "
                f"docker rmi -f {env_tag} >/dev/null 2>&1")
        subprocess.Popen(["sh", "-c", bomb], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)

    # 3) write run artifacts (run dir format)
    (out / "raw").mkdir(exist_ok=True)
    sess = logs / "session.jsonl"
    if sess.exists():
        (out / "raw" / "session.jsonl").write_bytes(sess.read_bytes())
    cost = _trace_cost(sess, args.model)
    log_usage("run.agent", args.model, cost["in"], cost["out"], skill)
    (out / "cost.json").write_text(json.dumps(cost, indent=2))
    (out / "agent_stdout.txt").write_text(stdout or "")
    manifest = {
        "run_id": run_id, "skill": skill, "prompt": prompt,
        "base_image": cand.get("base_image"), "env_image": env_tag,
        # the live container the Property Checker will exec into, then destroy:
        "container": run_id if container_alive else None,
        "container_alive": container_alive,
        "cleanup_grace_s": args.cleanup_grace,  # timebomb removes the container after this
        "case": str(case), "model": args.model,
        "termination": {"reason": termination, "rc": rc, "seconds": round(dt, 1)},
        "trace": "raw/session.jsonl", "workspace_diff": "logs/workspace.diff",
    }
    (out / "run.json").write_text(json.dumps(manifest, indent=2))

    print(f"\ndone rc={rc} ({termination}) in {dt:.1f}s")
    print(f"  trace:   {out}/raw/session.jsonl")
    print(f"  diff:    {out}/logs/workspace.diff")
    print(f"  cost:    {cost['turns']} turns, in/out={cost['in']}/{cost['out']}, ${cost['price_usd']}")
    print(f"  run.json: {out}/run.json")
    if container_alive:
        print(f"  container LEFT RUNNING for the property checker: {run_id}")
        print(f"  (timebomb: auto-removed in {args.cleanup_grace}s if the checker doesn't)")
        print(f"  → run: python -m skillrace.check_properties --run {out} --props skills/{skill}/properties.json")
    else:
        print("  (container destroyed — run did not complete; state checks unavailable)")


if __name__ == "__main__":
    main()
