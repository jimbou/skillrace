"""Runner for a generated test case — a SEPARATE command from the generator.

The generator writes cases (Dockerfile + candidate.json) to a dir and stops. This
command takes ONE saved case and does the actual run: build the env image, run the
AGENT UNDER TEST (the skill, baked into the base image) on the case's prompt, and
capture the trace + cost + a workspace diff into a run dir.

Decoupled by design: generate whenever; run any case later, on demand, with the
inputs you choose (which case, which agent model, where to put the run).

Usage:
  python -m skillrace.run_case --case out/genagent/cases/case2 \
      --skill-dir skills/fix-failing-test \
      --model qwen3.6-flash --out runs/ftt-case2
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import re
import shlex
import subprocess
import time
import uuid

from .closeai import PRICES, log_usage
from .candidate_policy import validate_candidate_containerfile
from .io_utils import atomic_write_json
from .runtime_trust import verify_runtime_integrity


_SKILL_IDENTIFIER_RE = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,127})?\Z")
AGENT_STARTED_MARKER = "agent-started"
TRUSTED_SKILL_MOUNT = "/trusted-skill"
TRUSTED_PI_EXECUTABLE = "/usr/local/bin/pi"


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


def preserve_status_script(agent_command: str, cleanup_command: str) -> str:
    """Run cleanup while returning exactly the agent command's exit status."""
    return (
        "set +e\n"
        "(\n"
        f"{agent_command}\n"
        ")\n"
        "agent_rc=$?\n"
        "(\n"
        f"{cleanup_command}\n"
        ")\n"
        'exit "$agent_rc"\n'
    )


def validate_skill_identifier(skill: str) -> str:
    """Reject candidate-controlled values that are not simple skill IDs."""
    if not isinstance(skill, str) or not _SKILL_IDENTIFIER_RE.fullmatch(skill):
        raise ValueError(f"invalid skill identifier: {skill!r}")
    return skill


def agent_started_from_logs(logs_dir: str | pathlib.Path) -> bool:
    """Read the container-published proof that Pi invocation was reached."""
    return (pathlib.Path(logs_dir) / AGENT_STARTED_MARKER).is_file()


def build_agent_command(
    model: str,
    skill: str,
    *,
    workspace: str | pathlib.Path = "/workspace",
    started_marker: str | pathlib.Path = "/logs/agent-started",
    skill_path: str | pathlib.Path = TRUSTED_SKILL_MOUNT,
    pi_executable: str | pathlib.Path = TRUSTED_PI_EXECUTABLE,
    git_executable: str | pathlib.Path = "/usr/bin/git",
) -> str:
    """Build the inner agent command with candidate and CLI values shell-safe."""
    skill = validate_skill_identifier(skill)
    model_arg = shlex.quote(model)
    skill_path = shlex.quote(str(skill_path))
    pi_arg = shlex.quote(str(pi_executable))
    workspace_arg = shlex.quote(str(workspace))
    marker_arg = shlex.quote(str(started_marker))
    return (
        f"cd {workspace_arg} || exit 125; "
        f"test -x {pi_arg} || exit 127; "
        f": > {marker_arg} || exit 125; "
        f"{pi_arg} --provider closeai --model {model_arg} --print "
        f"--session /logs/session.jsonl --skill {skill_path} "
        '"$PI_PROMPT" </dev/null'
    )


def build_workspace_setup_command(
    *, workspace: str | pathlib.Path = "/workspace",
    git_executable: str | pathlib.Path = "/usr/bin/git",
) -> str:
    workspace_arg = shlex.quote(str(workspace))
    git_arg = shlex.quote(str(git_executable))
    return (
        f"cd {workspace_arg} || exit 125; "
        f"{git_arg} add -A || exit 125; "
        f"{git_arg} commit -q -m 'skillrace: pre-agent baseline' || true"
    )


def build_workspace_cleanup_command(
    *, workspace: str | pathlib.Path = "/workspace",
    git_executable: str | pathlib.Path = "/usr/bin/git",
) -> str:
    workspace_arg = shlex.quote(str(workspace))
    git_arg = shlex.quote(str(git_executable))
    return (
        f"cd {workspace_arg} && {git_arg} add -A && "
        f"{git_arg} diff --cached HEAD > /logs/workspace.diff 2>/dev/null || true"
    )


def _trusted_skill_dir(path: str | pathlib.Path) -> pathlib.Path:
    root = pathlib.Path(path).resolve()
    if not root.is_dir() or not (root / "SKILL.md").is_file():
        raise ValueError(f"trusted skill directory must contain SKILL.md: {root}")
    return root


def build_container_start_args(run_id, logs, image, skill_dir):
    trusted_skill = _trusted_skill_dir(skill_dir)
    return [
        "docker", "run", "-d", "--name", run_id, "--network=host",
        "--no-healthcheck", "--entrypoint", "/bin/sleep",
        "-v", f"{pathlib.Path(logs).resolve()}:/logs",
        "-v", f"{trusted_skill}:{TRUSTED_SKILL_MOUNT}:ro",
        image, "infinity",
    ]


def build_agent_exec_args(run_id, command):
    if not isinstance(command, str) or "\x00" in command:
        raise ValueError("agent command must be safe text")
    return [
        "docker", "exec",
        "-e", "CLOSE_API_KEY",
        "-e", "PI_PROMPT",
        run_id, "/bin/bash", "-c", command,
    ]


def build_agent_exec_environment(api_key, prompt, *, base_environment=None):
    for name, value in (("CLOSE_API_KEY", api_key), ("PI_PROMPT", prompt)):
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError(f"{name} must be safe text")
    environment = dict(os.environ if base_environment is None else base_environment)
    environment["CLOSE_API_KEY"] = api_key
    environment["PI_PROMPT"] = prompt
    return environment


def build_plain_exec_args(run_id, command):
    if not isinstance(command, str) or "\x00" in command:
        raise ValueError("container command must be safe text")
    return ["docker", "exec", run_id, "/bin/bash", "-c", command]


def finalize_run(path: str | pathlib.Path, manifest: dict, rc: int) -> None:
    """Publish the final manifest before propagating a nonzero agent status."""
    atomic_write_json(path, manifest)
    if rc != 0:
        raise SystemExit(rc)


def main():
    ap = argparse.ArgumentParser(description="Run one generated test case (agent under test)")
    ap.add_argument("--case", required=True, help="case dir (Dockerfile + candidate.json)")
    ap.add_argument("--skill-dir", required=True,
                    help="host repository skill directory mounted read-only")
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
    dockerfile = (case / "Dockerfile").read_text()
    if cand.get("containerfile") is not None and cand["containerfile"] != dockerfile:
        raise SystemExit("candidate Dockerfile does not match candidate.json")
    try:
        validate_candidate_containerfile(dockerfile, cand["base_image"])
    except ValueError as error:
        raise SystemExit(f"candidate Dockerfile policy rejection: {error}") from error
    raw_skill = cand.get("skill") or cand["base_image"].split("/")[-1].split(":")[0]
    skill = validate_skill_identifier(raw_skill)
    prompt = cand["prompt"]

    out = pathlib.Path(args.out)
    (out / "logs").mkdir(parents=True, exist_ok=True)
    logs = (out / "logs").resolve()
    (logs / AGENT_STARTED_MARKER).unlink(missing_ok=True)
    run_id = "run-" + uuid.uuid4().hex[:12]
    env_tag = f"skillrace/runenv-{run_id}"

    # 1) build the env image from the case's Dockerfile
    print(f"building env image from {case}/Dockerfile ...")
    ok, berr = _docker_build(case, env_tag)
    if not ok:
        raise SystemExit(f"env build failed:\n{berr[-1500:]}")
    try:
        verify_runtime_integrity(cand["base_image"], env_tag)
    except Exception as error:
        raise SystemExit(f"pre-agent runtime integrity failure: {error}") from error

    # 2) run the agent under test (skill is baked into the base at /skills/<skill>).
    #    capture the session trace + a post-run workspace diff. --rm + --name so a
    #    timeout can tear the container down.
    # commit the post-setup state as the baseline, so the post-agent diff shows
    # exactly the agent's changes (incl. files it creates), regardless of how the
    # case Dockerfile was built.
    agent_command = build_agent_command(args.model, skill)
    # Start a LONG-LIVED container (sleep infinity), run the agent via `docker exec`,
    # and LEAVE the container running. The Property Checker stages immutable evidence,
    # commits one final-filesystem snapshot, and runs each check in a fresh networkless
    # child before destroying this container and the temporary snapshot.
    subprocess.run(build_container_start_args(
                       run_id, logs, env_tag, args.skill_dir
                   ),
                   check=True, capture_output=True)
    setup = subprocess.run(
        build_plain_exec_args(run_id, build_workspace_setup_command()),
        capture_output=True,
        text=True,
    )
    if setup.returncode != 0:
        subprocess.run(["docker", "rm", "-f", run_id], capture_output=True)
        raise SystemExit(f"pre-agent workspace setup failed: {setup.stderr[-500:]}")
    print(f"running agent under test ({args.model}) on skill {skill!r} ...")
    t0 = time.time()
    termination, container_alive = "completed", True
    try:
        p = subprocess.run(build_agent_exec_args(run_id, agent_command),
                           env=build_agent_exec_environment(
                               os.environ["CLOSE_API_KEY"], prompt
                           ),
                           capture_output=True, text=True, timeout=args.wall_clock)
        rc, stdout = p.returncode, p.stdout
        termination = "completed" if rc == 0 else "error"
    except subprocess.TimeoutExpired:
        rc, stdout, termination = 124, "", "timeout"
        subprocess.run(["docker", "rm", "-f", run_id], capture_output=True)
        container_alive = False
    dt = time.time() - t0
    agent_started = agent_started_from_logs(logs)

    if container_alive:
        subprocess.run(
            build_plain_exec_args(run_id, build_workspace_cleanup_command()),
            capture_output=True,
            text=True,
        )

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
        "agent_started": agent_started,
        "termination": {"reason": termination, "rc": rc, "seconds": round(dt, 1)},
        "trace": "raw/session.jsonl", "workspace_diff": "logs/workspace.diff",
    }
    print(f"\ndone rc={rc} ({termination}) in {dt:.1f}s")
    print(f"  trace:   {out}/raw/session.jsonl")
    print(f"  diff:    {out}/logs/workspace.diff")
    print(f"  cost:    {cost['turns']} turns, in/out={cost['in']}/{cost['out']}, ${cost['price_usd']}")
    print(f"  run.json: {out}/run.json")
    if container_alive:
        print(f"  container LEFT RUNNING for the property checker: {run_id}")
        print(f"  (timebomb: auto-removed in {args.cleanup_grace}s if the checker doesn't)")
        print(f"  → run: python -m skillrace.check_properties --run {out}")
    else:
        print("  (container destroyed — run did not complete; state checks unavailable)")
    finalize_run(out / "run.json", manifest, rc)


if __name__ == "__main__":
    main()
