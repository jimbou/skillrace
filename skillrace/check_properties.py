"""Property checker — a SEPARATE command, run after a test executes.

Given a run dir (from run_case) and a skill's NL properties, for each property the
model writes a BASH SCRIPT that mechanically checks it, grounded in a SNAPSHOT of the
finished run (file tree + changed files + tool-call trace + the tools that actually
exist in the container). The script RUNS INSIDE the run's live container and decides
the verdict by its EXIT CODE: exit 0 = the property HOLDS, non-zero = VIOLATED. The
model runs ONLY to author the script; the script itself runs mechanically.

The script can test the property ANY way it sees fit, because the container exposes:
  - /workspace            the final project state (the agent's result), a git repo
  - /check/trace.jsonl    the agent's full session trace (JSONL)
  - /check/workspace.diff  the git diff of everything the agent changed
So a single mechanism (bash in the container) covers BOTH final-state properties and
trace/diff properties — there is no separate "state" vs "trace" check kind anymore.
The authored scripts are saved under <run>/checks/<property_id>.sh as inspectable
artifacts.

Usage:
  python -m skillrace.check_properties --run runs/ftt-case2 \
      --props skills/fix-failing-test/properties.json
"""
from __future__ import annotations
import argparse
import json
import pathlib
import re
import subprocess

from .closeai import chat

SCRIPT_SYS = (
    "You write a BASH SCRIPT that mechanically checks ONE natural-language property "
    "about a FINISHED coding-agent run. The script runs INSIDE the run's container. "
    "Decide the verdict by EXIT CODE: `exit 0` if the property HOLDS, a NON-ZERO exit "
    "if it is VIOLATED. Test the property ANY way you see fit with the tools available "
    "— grep, awk, sed, find, node, python, run the project, etc.\n"
    "The container gives you:\n"
    "  /workspace            the FINAL project state (the agent's result), a git repo\n"
    "  /check/trace.jsonl    the agent's full session trace (JSONL; each line a message, "
    "assistant messages carry content blocks incl. {\"type\":\"toolCall\",\"name\":..,"
    "\"arguments\":..} and reasoning/text)\n"
    "  /check/workspace.diff  the git diff of everything the agent changed\n"
    "Rules:\n"
    "  - Start with `#!/usr/bin/env bash` (you may `set -u`, but do NOT `set -e` blindly "
    "— you want to control the exit code yourself).\n"
    "  - Use ONLY tools listed as available; if a tool is absent (e.g. python on a node "
    "image) use one that exists.\n"
    "  - If the property is conditional and its precondition is absent (e.g. \"IF there "
    "is motion ...\" and there is none), treat it as VACUOUSLY HOLDING → exit 0.\n"
    "  - For a behavior property, assert the CONCRETE expected outcome where computable, "
    "not just that a file exists.\n"
    "  - echo a one-line reason for the verdict before exiting.\n"
    "Output ONLY the bash script — no markdown fences, no prose around it."
)

# Interpreters/tools a script might reach for — probed in the actual container so the
# author never assumes a tool that isn't there (e.g. python on a node base).
_PROBE_TOOLS = ["python3", "python", "node", "bash", "grep", "sed", "awk", "find",
                "test", "jq", "pytest", "npm", "npx", "ripgrep", "rg"]


def _load_trace(run_dir):
    """Return ordered tool_calls [(name, text)] for the AUTHORING context only."""
    calls = []
    sess = run_dir / "raw" / "session.jsonl"
    if not sess.exists():
        return calls
    for line in open(sess):
        try:
            m = json.loads(line).get("message", {})
        except Exception:
            continue
        if m.get("role") != "assistant":
            continue
        for b in m.get("content", []):
            if b.get("type") == "toolCall":
                a = b.get("arguments", {}) or {}
                text = a.get("command") or a.get("path") or json.dumps(a)
                calls.append((b.get("name", ""), f"{b.get('name','')} {text}"))
    return calls


def _changed_files(run_dir):
    diff = run_dir / "logs" / "workspace.diff"
    if not diff.exists():
        return []
    files = set()
    for line in open(diff, errors="replace"):
        m = re.match(r"diff --git a/(\S+) b/(\S+)", line)
        if m:
            files.add(m.group(2))
    return sorted(files)


def _file_tree(container):
    if not container:
        return []
    p = subprocess.run(["docker", "exec", container, "sh", "-c",
                        "cd /workspace && find . -type f -not -path './.git/*' | head -200"],
                       capture_output=True, text=True)
    return [x[2:] if x.startswith("./") else x for x in p.stdout.splitlines()]


def _available_tools(container):
    if not container:
        return []
    cmd = "for t in " + " ".join(_PROBE_TOOLS) + "; do command -v $t >/dev/null 2>&1 && echo $t; done"
    p = subprocess.run(["docker", "exec", container, "sh", "-c", cmd],
                       capture_output=True, text=True)
    return p.stdout.split()


def _strip_fences(s):
    s = s.strip()
    if s.startswith("```"):
        # drop the opening fence line (``` or ```bash) and the closing fence
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip() + "\n"


def _syntax_ok(script_path):
    """True if the script parses as bash (`bash -n`). Returns (ok, error_text)."""
    p = subprocess.run(["bash", "-n", str(script_path)], capture_output=True, text=True)
    return p.returncode == 0, (p.stderr or "").strip()


def author_script(prop, snapshot, model, fix=None):
    """Ask the model to WRITE a bash check script. Returns (script_text, cost_usd).
    If `fix` = (broken_script, bash_error) is given, ask the model to CORRECT it
    instead (a temp-0 re-author would otherwise reproduce the same broken script)."""
    user = (
        f"PROPERTY (kind hint: {prop.get('reads', '?')}):\n{prop['nl']}\n\n"
        f"SKILL: {snapshot['skill']}\nPROMPT: {snapshot['prompt']}\n\n"
        f"TOOLS AVAILABLE IN THE CONTAINER (use ONLY these):\n  "
        + (" ".join(snapshot.get("tools", [])) or "(unknown — assume POSIX sh + grep)") + "\n\n"
        f"FINAL FILE TREE (/workspace):\n" + "\n".join(snapshot["file_tree"][:80]) + "\n\n"
        f"FILES THE AGENT CHANGED:\n" + ("\n".join(snapshot["changed"]) or "(none)") + "\n\n"
        f"TOOL CALLS (in order):\n" +
        "\n".join(f"- {t}" for _, t in snapshot["tool_calls"][:60]) + "\n\n"
        "Write the bash script."
    )
    if fix:
        broken, err = fix
        user += (f"\n\nYour PREVIOUS script FAILED `bash -n` with this error:\n{err}\n\n"
                 f"--- previous script ---\n{broken}\n--- end ---\n"
                 f"Return a CORRECTED script that parses cleanly. Output ONLY the script.")
    resp = chat([{"role": "system", "content": SCRIPT_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, reasoning=True, max_tokens=1400,
                tag="check.author", skill=snapshot.get("skill"))
    return _strip_fences(resp["content"]), resp["cost_usd"]


def run_script(script_path, container):
    """Copy the script into the container and run it. Returns (holds, detail)."""
    if not container:
        return None, "no live container (run did not leave one; re-run the case)"
    dst = f"/check/{script_path.name}"
    cp = subprocess.run(["docker", "cp", str(script_path), f"{container}:{dst}"],
                        capture_output=True, text=True)
    if cp.returncode != 0:
        return None, f"docker cp failed: {cp.stderr.strip()[:160]}"
    p = subprocess.run(["docker", "exec", container, "bash", dst],
                       capture_output=True, text=True)
    out = (p.stdout + p.stderr).strip().splitlines()
    tail = out[-1] if out else ""
    return (p.returncode == 0), f"exit={p.returncode}; {tail[:160]!r}"


def main():
    ap = argparse.ArgumentParser(description="Author + run bash property checks over a finished run")
    ap.add_argument("--run", required=True, help="run dir from run_case")
    ap.add_argument("--props", required=True, help="skill properties.json (NL)")
    ap.add_argument("--model", default="qwen3.6-flash")
    ap.add_argument("--keep-container", action="store_true",
                    help="do not destroy the run's container after checking (for debugging)")
    args = ap.parse_args()

    run_dir = pathlib.Path(args.run)
    manifest = json.loads((run_dir / "run.json").read_text())
    props = json.loads(pathlib.Path(args.props).read_text())
    container = manifest.get("container")

    # Stage the trace + diff INSIDE the container at /check so scripts can read them
    # alongside /workspace (one unified mechanism for state- and trace-based checks).
    if container:
        subprocess.run(["docker", "exec", container, "mkdir", "-p", "/check"],
                       capture_output=True)
        for host_rel, dst in [("raw/session.jsonl", "/check/trace.jsonl"),
                              ("logs/workspace.diff", "/check/workspace.diff")]:
            hp = run_dir / host_rel
            if hp.exists():
                subprocess.run(["docker", "cp", str(hp), f"{container}:{dst}"],
                               capture_output=True)

    snapshot = {
        "skill": manifest.get("skill"), "prompt": manifest.get("prompt"),
        "container": container,
        "tools": _available_tools(container),
        "file_tree": _file_tree(container),
        "changed": _changed_files(run_dir),
        "tool_calls": _load_trace(run_dir),
    }

    checks_dir = run_dir / "checks"
    checks_dir.mkdir(exist_ok=True)
    verdicts, cost = [], 0.0
    for prop in props:
        # One flaky model call (e.g. an API read timeout) must NOT abort the whole
        # checker — record an inconclusive verdict for that property and continue.
        try:
            script, c = author_script(prop, snapshot, args.model)
            cost += c
        except Exception as e:  # noqa: BLE001 — degrade, don't crash
            print(f"  [author failed] {prop['id']}: {type(e).__name__}: {e}")
            verdicts.append({"property_id": prop["id"], "holds": None,
                             "violated": False, "detail": f"author failed: {type(e).__name__}",
                             "script": None})
            continue
        sp = checks_dir / f"{prop['id']}.sh"
        sp.write_text(script)
        # A SYNTAX-BROKEN script must NEVER masquerade as a violation: validate with
        # `bash -n`, and if it doesn't parse, re-author ONCE with the error fed back
        # (a temp-0 plain retry would reproduce the same broken script). If it still
        # won't parse, record INCONCLUSIVE rather than a false violation.
        ok, serr = _syntax_ok(sp)
        if not ok:
            try:
                script, c2 = author_script(prop, snapshot, args.model, fix=(script, serr))
                cost += c2
                sp.write_text(script)
                ok, serr = _syntax_ok(sp)
            except Exception as e:  # noqa: BLE001
                serr = f"{serr} | re-author failed: {type(e).__name__}"
        if not ok:
            print(f"  [syntax broken] {prop['id']}: {serr[:120]}")
            verdicts.append({"property_id": prop["id"], "holds": None,
                             "violated": False,
                             "detail": f"script failed bash -n (inconclusive): {serr[:120]}",
                             "script": f"checks/{prop['id']}.sh"})
            continue
        holds, detail = run_script(sp, container)
        verdicts.append({"property_id": prop["id"], "holds": holds,
                         "violated": (holds is False), "detail": detail,
                         "script": f"checks/{prop['id']}.sh"})

    (run_dir / "verdicts.json").write_text(json.dumps(verdicts, indent=2))

    # the checker OWNS teardown: now that the scripts ran in the live container,
    # destroy it (and its env image) — keep --keep-container to skip for debugging.
    if container and not args.keep_container:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        if manifest.get("env_image"):
            subprocess.run(["docker", "rmi", "-f", manifest["env_image"]], capture_output=True)

    print(f"properties checked (author cost ${cost:.4f}):\n")
    for v in verdicts:
        mark = "✓ holds" if v["holds"] else ("✗ VIOLATED" if v["violated"] else "? inconclusive")
        print(f"  [{mark}] {v['property_id']}  ({v['script']})")
        print(f"            {v['detail']}")
    if container and not args.keep_container:
        print(f"\ncleaned up container {container} + env image")


if __name__ == "__main__":
    main()
