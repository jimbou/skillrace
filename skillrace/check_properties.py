"""Property checker — a separate command run after an agent execution.

Verdicts come from three provenances, most trustworthy first:
  1. FIXED core (fixed_checks.py): universal invariants, pure Python on the host,
     zero model involvement. Always run.
  2. POST-RUN PATH-ONLY checks (compile_checks.py): per-run Python scripts authored
     from the task, property, environment description, tools, and final paths only.
     The author does not see artifact/trace contents, method identity, or verdicts.
  3. AUTHORED-POST-RUN (legacy, --author-post-hoc): the model writes the script
     while looking at a snapshot of the finished run. Weaker oracle-integrity
     claim; kept for ad-hoc debugging of old runs only.

The checker snapshots the finished container once, then every bash script runs in its
own fresh, networkless child of that snapshot. This prevents one oracle script from
mutating evidence seen by the next. A host timeout makes a hung check inconclusive.
Python exit 0 = HOLDS, 1 = VIOLATED, and 2 = NOT CONSIDERED. Each child exposes:
  - /workspace            the final project state (the agent's result), a git repo
  - /check/trace.jsonl    the agent's full session trace (JSONL)
  - /check/workspace.diff  the git diff of everything the agent changed

Usage:
  python -m skillrace.check_properties --run runs/ftt-case2 \
      --post-run-input runs/ftt-case2/post-run-check-input.json
  python -m skillrace.check_properties --run runs/old-run \
      --props skills/fix-failing-test/properties.json --author-post-hoc
"""
from __future__ import annotations
import atexit
import argparse
import json
import pathlib
import re
import subprocess
import uuid

from .closeai import OutcomeUnknownError, chat
from .compile_checks import compile_post_run_checks, validate_script_policy
from .fixed_checks import run_fixed_checks
from .io_utils import atomic_write_json, atomic_write_text


DEFAULT_CHECK_TIMEOUT_SECONDS = 60

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
    "  - Never use the network, install packages, invoke Docker/Podman, sudo, mount, "
    "or change the sandbox.\n"
    "  - If reading /check/trace.jsonl, parse JSONL structurally and inspect exact "
    "toolCall blocks; raw grep can confuse reasoning text with executed commands.\n"
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
    error = (p.stderr or "").strip()
    return p.returncode == 0 and not error, error


def author_script(prop, snapshot, model, fix=None):
    """Ask the model to WRITE a bash check script. Returns (script_text, cost_provider_credits).
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
        user += (f"\n\nYour PREVIOUS script FAILED mechanical validation:\n{err}\n\n"
                 f"--- previous script ---\n{broken}\n--- end ---\n"
                 f"Return a CORRECTED script that parses cleanly. Output ONLY the script.")
    resp = chat([{"role": "system", "content": SCRIPT_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, reasoning=False, max_tokens=1400,
                tag="check.author", skill=snapshot.get("skill"))
    return _strip_fences(resp["content"]), resp["cost_provider_credits"]


def check_container_command(name: str, image: str) -> list[str]:
    """Create one disposable, networkless property-check container."""

    return [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=256",
        "--entrypoint",
        "/bin/sleep",
        image,
        "300",
    ]


def snapshot_container_for_checks(container: str) -> str:
    """Commit the finished filesystem once; each check starts from this snapshot."""

    image = "skillrace/property-check-snapshot-" + uuid.uuid4().hex[:12]
    process = subprocess.run(
        ["docker", "commit", "--pause=true", container, image],
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "unknown Docker error").strip()
        raise RuntimeError(f"final-state snapshot failed: {detail[-300:]}")
    return image


def image_identity(image: str) -> str:
    process = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
    )
    identity = process.stdout.strip()
    if process.returncode != 0 or not identity:
        detail = (process.stderr or process.stdout or "unknown error").strip()
        raise RuntimeError(f"checker snapshot identity failed: {detail[-160:]}")
    return identity


def run_script_isolated(script_path, snapshot_image, *, timeout_seconds):
    """Run one script in a fresh child. Timeout/infra failures are inconclusive."""

    if not snapshot_image:
        return None, "no final-state snapshot (run did not leave a live container)"
    name = "skillrace-property-check-" + uuid.uuid4().hex[:12]
    start = subprocess.run(
        check_container_command(name, snapshot_image), capture_output=True, text=True
    )
    if start.returncode != 0:
        detail = (start.stderr or start.stdout or "unknown Docker error").strip()
        return None, f"isolated check container failed: {detail[-160:]}"
    dst = f"/check/{script_path.name}"
    try:
        prepare = subprocess.run(
            ["docker", "exec", name, "mkdir", "-p", "/check"],
            capture_output=True,
            text=True,
        )
        if prepare.returncode != 0:
            return None, f"check staging failed: {prepare.stderr.strip()[:160]}"
        # ``docker cp`` preserves the host numeric owner and mode. Our crash-safe
        # atomic writer intentionally creates mode-0600 files owned by the host user;
        # after ``--cap-drop=ALL``, container root cannot bypass that foreign UID's
        # permissions. Stream the reviewed bytes through the container instead so
        # the staged copy is owned by the checker process while the original hash and
        # immutable manifest remain authoritative.
        try:
            script_bytes = pathlib.Path(script_path).read_bytes()
        except OSError as error:
            return None, f"check script read failed: {str(error)[:160]}"
        stage = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                name,
                "sh",
                "-c",
                'umask 077 && cat > "$1"',
                "sh",
                dst,
            ],
            input=script_bytes,
            capture_output=True,
        )
        if stage.returncode != 0:
            detail = stage.stderr.decode("utf-8", errors="replace").strip()
            return None, f"check staging failed: {detail[:160]}"
        try:
            interpreter = "python3" if script_path.suffix == ".py" else "bash"
            process = subprocess.run(
                ["docker", "exec", name, interpreter, dst],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return None, f"check timeout after {timeout_seconds:g}s (inconclusive)"
        output = (process.stdout + process.stderr).strip().splitlines()
        tail = output[-1] if output else ""
        if script_path.suffix == ".py":
            holds = {0: True, 1: False, 2: None}.get(process.returncode)
        else:
            holds = process.returncode == 0
        return holds, f"exit={process.returncode}; {tail[:160]!r}"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def run_script(script_path, container, timeout_seconds=DEFAULT_CHECK_TIMEOUT_SECONDS):
    """Compatibility wrapper: snapshot a container and run one isolated check."""

    if not container:
        return None, "no live container (run did not leave one; re-run the case)"
    snapshot = snapshot_container_for_checks(container)
    try:
        return run_script_isolated(
            script_path, snapshot, timeout_seconds=timeout_seconds
        )
    finally:
        subprocess.run(["docker", "rmi", "-f", snapshot], capture_output=True)


def _fixed_allowlist(compiled_manifest):
    """Return None only for legacy/RQ3 manifests with no applicability record."""
    if not compiled_manifest or "applicability" not in compiled_manifest:
        return None
    applicability = compiled_manifest["applicability"]
    fixed = applicability.get("fixed_invariants") if isinstance(applicability, dict) else None
    if not isinstance(fixed, list) or any(not isinstance(item, str) for item in fixed):
        raise ValueError("compiled applicability.fixed_invariants must be a string list")
    return fixed


def main():
    ap = argparse.ArgumentParser(description="Run property checks over a finished run")
    ap.add_argument("--run", required=True, help="run dir from run_case")
    ap.add_argument("--props", help="skill properties.json (NL) — needed only for --author-post-hoc")
    ap.add_argument("--checks", help="dir of precompiled check scripts "
                                     "(default: <case>/checks from run.json)")
    ap.add_argument("--author-post-hoc", action="store_true",
                    help="LEGACY: author scripts from a post-run snapshot (weaker oracle)")
    ap.add_argument("--model", default="glm-4.5-flash")
    ap.add_argument(
        "--post-run-input",
        help="JSON input for blinded post-run Python checker generation",
    )
    ap.add_argument(
        "--verdict-provenance",
        choices=("compiled-pre-run", "hidden-independent"),
        default="compiled-pre-run",
        help="provenance label for precommitted checks; hidden evaluation uses "
        "hidden-independent because the checks were authored outside campaign feedback",
    )
    ap.add_argument("--keep-container", action="store_true",
                    help="do not destroy the run's container after checking (for debugging)")
    ap.add_argument(
        "--check-timeout",
        type=float,
        help="seconds per mechanical check (default: compiled policy, otherwise 60)",
    )
    args = ap.parse_args()

    run_dir = pathlib.Path(args.run)
    manifest = json.loads((run_dir / "run.json").read_text())
    container = manifest.get("container")
    post_run_input = None
    if args.post_run_input:
        post_run_input = json.loads(pathlib.Path(args.post_run_input).read_text())
        if post_run_input.get("schema") != "post-run-check-input/1":
            raise SystemExit("malformed post-run checker input")

    # resolve the precompiled checks dir: explicit flag, else alongside the case
    precompiled = pathlib.Path(args.checks) if args.checks else None
    if precompiled is None and post_run_input is None and manifest.get("case"):
        cand = pathlib.Path(manifest["case"]) / "checks"
        if cand.is_dir():
            precompiled = cand
    if precompiled is None and not args.author_post_hoc and post_run_input is None:
        raise SystemExit("no precompiled checks found for this run's case — run "
                         "`python -m skillrace.compile_checks --case <case> --props <props>` "
                         "first, or pass --author-post-hoc (legacy).")

    compiled_manifest = None
    if precompiled is not None:
        man_path = precompiled / "manifest.json"
        if man_path.exists():
            compiled_manifest = json.loads(man_path.read_text())

    policy = (
        compiled_manifest.get("execution_policy", {})
        if isinstance(compiled_manifest, dict)
        else {}
    )
    check_timeout = args.check_timeout
    if check_timeout is None:
        check_timeout = policy.get("timeout_seconds", DEFAULT_CHECK_TIMEOUT_SECONDS)
    if (
        isinstance(check_timeout, bool)
        or not isinstance(check_timeout, (int, float))
        or check_timeout <= 0
        or check_timeout > 3600
    ):
        raise SystemExit("--check-timeout must be in (0, 3600] seconds")

    # The active post-run Python contract exposes trace evidence but not the diff.
    # Legacy precompiled/RQ3 checks keep their historical trace+diff interface.
    if container:
        subprocess.run(["docker", "exec", container, "mkdir", "-p", "/check"],
                       capture_output=True)
        evidence = [("raw/session.jsonl", "/check/trace.jsonl")]
        if post_run_input is None:
            evidence.append(("logs/workspace.diff", "/check/workspace.diff"))
        for host_rel, dst in evidence:
            hp = run_dir / host_rel
            if hp.exists():
                subprocess.run(["docker", "cp", str(hp), f"{container}:{dst}"],
                               capture_output=True)

    snapshot_image = None
    snapshot_error = None
    final_tree = _file_tree(container) if post_run_input is not None else []
    available_tools = _available_tools(container) if post_run_input is not None else []
    if container:
        try:
            snapshot_image = snapshot_container_for_checks(container)
        except RuntimeError as error:
            snapshot_error = str(error)
    if snapshot_image:
        cleanup_snapshot = lambda: subprocess.run(  # noqa: E731 - atexit handle
            ["docker", "rmi", "-f", snapshot_image], capture_output=True
        )
        atexit.register(cleanup_snapshot)

    # 1) FIXED core — pure code on the host, zero model, always runs.
    fixed_manifest = compiled_manifest
    if post_run_input is not None and post_run_input.get("applicability") is not None:
        fixed_manifest = {"applicability": post_run_input["applicability"]}
    verdicts = run_fixed_checks(
        run_dir, applicable_ids=_fixed_allowlist(fixed_manifest)
    )
    cost = 0.0

    if precompiled is not None:
        # 2) COMPILED-PRE-RUN — execute the case's byte-identical scripts.
        entries = (compiled_manifest["checks"] if compiled_manifest is not None
                   else [{"property_id": p.stem, "script": p.name, "syntax_ok": True}
                         for p in sorted(precompiled.glob("*.sh"))])
        for e in entries:
            base = {"property_id": e["property_id"], "provenance": args.verdict_provenance,
                    "script": str(precompiled / e["script"])}
            if not e.get("syntax_ok", True) or not e.get("policy_ok", True):
                verdicts.append({**base, "holds": None, "violated": False,
                                 "detail": f"compiled script failed validation (inconclusive): "
                                           f"{e.get('error', '')}"})
                continue
            if snapshot_error:
                holds, detail = None, snapshot_error
            else:
                holds, detail = run_script_isolated(
                    precompiled / e["script"],
                    snapshot_image,
                    timeout_seconds=check_timeout,
                )
            verdicts.append({**base, "holds": holds, "violated": (holds is False),
                             "detail": detail,
                             "isolation": "fresh-final-snapshot",
                             "timeout_seconds": check_timeout})
    elif post_run_input is not None:
        if snapshot_error or not snapshot_image:
            detail = snapshot_error or "no final-state snapshot"
            for prop in post_run_input["properties"]:
                verdicts.append(
                    {
                        "property_id": prop["id"],
                        "holds": None,
                        "violated": False,
                        "not_considered": True,
                        "detail": detail,
                        "script": None,
                        "provenance": "post-run-path-only",
                    }
                )
        else:
            try:
                compiled_manifest, cost = compile_post_run_checks(
                    run_dir=run_dir,
                    properties=post_run_input["properties"],
                    candidate=post_run_input["candidate"],
                    tools=available_tools,
                    final_tree=final_tree,
                    snapshot_identity=image_identity(snapshot_image),
                    model=args.model,
                    applicability=post_run_input.get("applicability"),
                )
            except OutcomeUnknownError as error:
                atomic_write_json(
                    run_dir / "checker-outcome-unknown.json",
                    {"schema": "checker-outcome-unknown/1", "error": str(error)[:500]},
                )
                if snapshot_image:
                    subprocess.run(
                        ["docker", "rmi", "-f", snapshot_image], capture_output=True
                    )
                    atexit.unregister(cleanup_snapshot)
                if container and not args.keep_container:
                    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
                    if manifest.get("env_image"):
                        subprocess.run(
                            ["docker", "rmi", "-f", manifest["env_image"]],
                            capture_output=True,
                        )
                raise SystemExit(75) from error
            checks_dir = run_dir / "checks"
            for excluded in compiled_manifest["excluded_properties"]:
                verdicts.append(
                    {
                        "property_id": excluded["property_id"],
                        "holds": None,
                        "violated": False,
                        "not_considered": True,
                        "detail": (
                            f"checker excluded: {excluded['reason']}; "
                            f"{excluded.get('error', '')}"
                        )[:300],
                        "script": None,
                        "provenance": "post-run-path-only",
                    }
                )
            for entry in compiled_manifest["checks"]:
                holds, detail = run_script_isolated(
                    checks_dir / entry["script"],
                    snapshot_image,
                    timeout_seconds=check_timeout,
                )
                verdicts.append(
                    {
                        "property_id": entry["property_id"],
                        "holds": holds,
                        "violated": holds is False,
                        "not_considered": holds is None,
                        "detail": detail,
                        "script": str(checks_dir / entry["script"]),
                        "provenance": "post-run-path-only",
                        "isolation": "fresh-final-snapshot",
                        "timeout_seconds": check_timeout,
                    }
                )
    else:
        # 3) LEGACY post-hoc authoring — the model sees the finished run.
        if not args.props:
            raise SystemExit("--author-post-hoc requires --props")
        props = json.loads(pathlib.Path(args.props).read_text())
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
                                 "script": None, "provenance": "authored-post-run"})
                continue
            sp = checks_dir / f"{prop['id']}.sh"
            atomic_write_text(sp, script)
            # A SYNTAX-BROKEN script must NEVER masquerade as a violation: validate with
            # `bash -n`, and if it doesn't parse, re-author ONCE with the error fed back
            # (a temp-0 plain retry would reproduce the same broken script). If it still
            # won't parse, record INCONCLUSIVE rather than a false violation.
            syntax_ok, syntax_error = _syntax_ok(sp)
            policy_ok, policy_error = validate_script_policy(
                script,
                snapshot.get("tools", []),
                reads=prop.get("reads"),
                property_nl=prop.get("nl"),
            )
            if not syntax_ok or not policy_ok:
                validation_error = " | ".join(
                    value
                    for value in (
                        f"bash -n: {syntax_error}" if not syntax_ok else "",
                        f"policy: {policy_error}" if not policy_ok else "",
                    )
                    if value
                )
                try:
                    script, c2 = author_script(
                        prop, snapshot, args.model, fix=(script, validation_error)
                    )
                    cost += c2
                    atomic_write_text(sp, script)
                    syntax_ok, syntax_error = _syntax_ok(sp)
                    policy_ok, policy_error = validate_script_policy(
                        script,
                        snapshot.get("tools", []),
                        reads=prop.get("reads"),
                        property_nl=prop.get("nl"),
                    )
                except Exception as e:  # noqa: BLE001
                    policy_error = (
                        f"{policy_error} | re-author failed: {type(e).__name__}"
                    )
            if not syntax_ok or not policy_ok:
                error = " | ".join(
                    value
                    for value in (
                        f"bash -n: {syntax_error}" if not syntax_ok else "",
                        f"policy: {policy_error}" if not policy_ok else "",
                    )
                    if value
                )
                print(f"  [check invalid] {prop['id']}: {error[:120]}")
                verdicts.append({"property_id": prop["id"], "holds": None,
                                 "violated": False,
                                 "detail": f"script failed validation (inconclusive): {error[:120]}",
                                 "script": f"checks/{prop['id']}.sh",
                                 "provenance": "authored-post-run"})
                continue
            if snapshot_error:
                holds, detail = None, snapshot_error
            else:
                holds, detail = run_script_isolated(
                    sp, snapshot_image, timeout_seconds=check_timeout
                )
            verdicts.append({"property_id": prop["id"], "holds": holds,
                             "violated": (holds is False), "detail": detail,
                             "script": f"checks/{prop['id']}.sh",
                             "provenance": "authored-post-run",
                             "isolation": "fresh-final-snapshot",
                             "timeout_seconds": check_timeout})

    atomic_write_json(run_dir / "verdicts.json", verdicts)

    if snapshot_image:
        subprocess.run(["docker", "rmi", "-f", snapshot_image], capture_output=True)
        atexit.unregister(cleanup_snapshot)

    # the checker OWNS teardown: now that the scripts ran in the live container,
    # destroy it (and its env image) — keep --keep-container to skip for debugging.
    if container and not args.keep_container:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        if manifest.get("env_image"):
            subprocess.run(["docker", "rmi", "-f", manifest["env_image"]], capture_output=True)

    print(f"properties checked (author cost ⚡{cost:.4f}):\n")
    for v in verdicts:
        mark = "✓ holds" if v["holds"] else ("✗ VIOLATED" if v["violated"] else "? inconclusive")
        print(f"  [{mark}] {v['property_id']}  ({v['script']})")
        print(f"            {v['detail']}")
    if container and not args.keep_container:
        print(f"\ncleaned up container {container} + env image")


if __name__ == "__main__":
    main()
