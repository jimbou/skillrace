"""Random / seed generator (baseline floor + SkillRACE seed phase).

Produces diverse, BUILDABLE `(prompt, env)` test cases for a skill with **no
behavioral feedback** — the floor baseline, and the bootstrap that seeds SkillRACE's
tree. Same component for both roles; only `provenance.source` differs.

Three model-driven steps (all DIRECT to CloseAI so temperature is controllable;
the agent-under-test is the only thing that runs via pi — D-PI-1):

  1. PROPOSE  (batch of K, high temp): SKILL.md + digest -> K natural-language IDEAS,
     each { summary, task, env }. summary is the dedup unit for the digest.
  2. REALIZE  (per item, temp 0): an idea + the skill-base context -> a concrete
     agent `prompt` AND a Containerfile `tail` (built on the per-skill base).
  3. BUILD + REPAIR: build the candidate image; if the build fails, feed the error
     back to the model to fix the tail and rebuild (a few times), else skip + count.

See docs/generator.md.
"""
from __future__ import annotations
import argparse
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from .closeai import chat, extract_json

TAIL_OPEN = "# >>> SKILLRACE TAIL >>>"
TAIL_CLOSE = "# <<< SKILLRACE TAIL <<<"
SETUP_COMMIT = ('RUN cd /workspace && git add -A && '
                'git commit -q -m "skillrace: test setup" || true')

PROPOSER_SYS = (
    "You design diverse TEST-CASE IDEAS for a coding-agent skill. Each idea is a "
    "(task, environment) pair described in plain natural language — NOT code, NOT a "
    "Dockerfile. **Derive the KIND of task and environment from the skill's stated "
    "purpose in the provided SKILL.md — do not assume a domain.** The TASK is the kind "
    "of thing to ask the agent to do that fits that purpose (illustrative only, across "
    "skills: 'build an HTML landing page about <topic> using <tools>', 'fix the failing "
    "tests', 'rebase this branch'). The ENVIRONMENT is the starting state the agent "
    "finds (illustrative: 'an empty project', 'a repo using <framework> version X', 'a "
    "project with this specific structure', 'a repo with <thing> in a broken state'). "
    "Each environment must be a GENUINE, UNSOLVED starting point — the task must still "
    "need doing. Explore VARIETY of tasks, tools, framework/versions, and structures."
)

REALIZER_SYS = (
    "You turn ONE test-case idea (a task + an environment, in natural language) into "
    "two concrete artifacts, grounded in the provided base image:\n"
    "  1. PROMPT — the exact instruction to give the coding agent (1-4 sentences).\n"
    "  2. TAIL  — Dockerfile instruction lines that BUILD the described environment "
    "ON TOP OF the base. EVERY shell command must be a Dockerfile instruction "
    "starting with RUN (or COPY/ENV/WORKDIR). Write files with a RUN heredoc, e.g.:\n"
    "    RUN cat > /workspace/<path> <<'EOF'\n    ...file contents...\n    EOF\n"
    "The base already provides the toolchain, git, the skill, and a /workspace "
    "project; CREATE from scratch anything the environment describes that isn't "
    "standard tooling (files/tests/configs, specific deps/versions, repo state). The "
    "TAIL must contain NO `FROM` line, no prose, no code fences.\n"
    "Stay faithful to the skill's purpose (read SKILL.md). The environment must be a "
    "GENUINE, UNSOLVED starting point: infer from the skill what 'unsolved' means and "
    "make the task truly require work — the target must NOT already be "
    "built/fixed/satisfied (e.g. a thing to fix is actually broken and its test "
    "genuinely fails; a thing to build does not yet exist).\n"
    'Return ONLY JSON: {"prompt": "...", "tail": "...the Dockerfile lines..."}'
)

REPAIR_SYS = (
    "You fix Dockerfile instruction lines (a TAIL applied on top of a base image) "
    "that FAILED to build. Given the failing tail and the build error, output ONLY "
    "the corrected instruction lines — no FROM line, no prose, no code fences."
)


# ---------------------------------------------------------------- context

def skill_context(skill_dir: pathlib.Path) -> str:
    """Grounding: SKILL.md (the purpose) + the base's /workspace contents."""
    parts = []
    md = skill_dir / "SKILL.md"
    if md.exists():
        parts.append("SKILL.md (the skill's purpose):\n" + md.read_text()[:1800])
    repo = skill_dir / "repo"
    if repo.is_dir():
        shown = []
        for p in sorted(repo.rglob("*")):
            if p.is_file():
                shown.append(f"--- /workspace/{p.relative_to(repo)} ---\n"
                             f"{p.read_text(errors='replace')[:800]}")
        if shown:
            parts.append("The base image ships these files at /workspace "
                         "(toolchain + git already installed):\n" + "\n".join(shown[:12]))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- model steps

def propose_batch(ctx, digest, k, model, temperature, reasoning=True, skill=None):
    """Call 1 -> list of {summary, task, env} (natural language ideas)."""
    avoid = "\n".join(f"- {s}" for s in digest) or "(none yet)"
    user = (
        f"{ctx}\n\n"
        f"Already-covered ideas (make NEW ones clearly distinct):\n{avoid}\n\n"
        f"Propose {k} NEW, diverse test-case ideas. Return ONLY a JSON array of {k} "
        f'objects with keys: "summary" (<=12 words), "task" (NL task to ask the '
        f'agent), "env" (NL description of the starting environment). No prose, no '
        f"code fences."
    )
    resp = chat([{"role": "system", "content": PROPOSER_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=temperature, max_tokens=2500, reasoning=reasoning,
                tag="generate.propose", skill=skill)
    items = extract_json(resp["content"])
    if not isinstance(items, list):
        raise ValueError("proposer did not return a JSON array")
    out = [{"summary": it["summary"].strip(), "task": it["task"].strip(),
            "env": it["env"].strip()}
           for it in items if all(key in it for key in ("summary", "task", "env"))]
    return out, resp


def realize(ctx, task, env, model, reasoning=True):
    """Call 2 -> (prompt, tail) grounded in the base. Returns (prompt, tail, cost)."""
    user = (f"{ctx}\n\nTEST-CASE IDEA:\n- task: {task}\n- environment: {env}\n\n"
            f'Return ONLY JSON {{"prompt": "...", "tail": "..."}}.')
    resp = chat([{"role": "system", "content": REALIZER_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, max_tokens=2200, reasoning=reasoning)
    obj = extract_json(resp["content"])
    prompt, tail = obj["prompt"].strip(), normalize_tail(_strip_fences(obj["tail"]))
    if _has_extra_from(tail):
        raise ValueError("realized tail contains a FROM instruction")
    return prompt, tail, resp["cost_usd"]


def repair_tail(ctx, tail, build_err, model, reasoning=True):
    """Fix a failing tail using the build error. Returns (fixed_tail, cost)."""
    user = (f"{ctx}\n\nThis TAIL (Dockerfile lines on top of the base) FAILED to "
            f"build:\n--- TAIL ---\n{tail}\n--- BUILD ERROR (last lines) ---\n"
            f"{build_err[-1500:]}\n\nOutput the corrected instruction lines only.")
    resp = chat([{"role": "system", "content": REPAIR_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, max_tokens=2000, reasoning=reasoning)
    fixed = normalize_tail(_strip_fences(resp["content"]))
    if _has_extra_from(fixed):
        raise ValueError("repaired tail contains a FROM instruction")
    return fixed, resp["cost_usd"]


# ---------------------------------------------------------------- assembly + build

def containerfile_for(base_image, tail):
    return f"FROM {base_image}\n{TAIL_OPEN}\n{tail}\n{SETUP_COMMIT}\n{TAIL_CLOSE}\n"


def build_image(containerfile, tag, timeout=900):
    """docker build the containerfile. Returns (ok, output)."""
    ctx = tempfile.mkdtemp(prefix="skillrace-build-")
    try:
        (pathlib.Path(ctx) / "Dockerfile").write_text(containerfile)
        p = subprocess.run(
            ["docker", "build", "--progress=plain", "-t", tag, "-f",
             f"{ctx}/Dockerfile", ctx],
            capture_output=True, text=True, timeout=timeout,
        )
        return p.returncode == 0, (p.stderr or p.stdout)
    except subprocess.TimeoutExpired:
        return False, "build timed out"
    finally:
        shutil.rmtree(ctx, ignore_errors=True)


def _strip_fences(s):
    s = s.strip()
    if s.startswith("```"):
        s = s.strip("`")
        for lang in ("dockerfile", "json", "sh", "bash"):
            if s.lower().startswith(lang):
                s = s[len(lang):]
                break
        s = s.strip()
    return s


_DOCKER_INSTR = ("RUN", "COPY", "ADD", "ENV", "WORKDIR", "ARG", "CMD", "ENTRYPOINT",
                 "LABEL", "USER", "EXPOSE", "VOLUME", "SHELL", "HEALTHCHECK",
                 "ONBUILD", "STOPSIGNAL", "FROM")


def normalize_tail(tail):
    """Deterministically prefix `RUN` to bare shell lines at instruction position
    (heredoc-aware), so a tail like `cat > f <<'EOF' ... EOF` becomes a valid
    Dockerfile. Lines already starting with a Dockerfile instruction, heredoc bodies,
    blanks, and comments are left untouched."""
    out, inside = [], None
    for raw in tail.splitlines():
        if inside is not None:
            out.append(raw)
            if raw.strip() == inside:
                inside = None
            continue
        s = raw.strip()
        is_instr = (not s) or s.startswith("#") or any(
            s.upper() == k or s.upper().startswith(k + " ") for k in _DOCKER_INSTR)
        out.append(raw if is_instr else "RUN " + raw)
        m = re.search(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?", raw)
        if m:
            inside = m.group(1)
    return "\n".join(out)


def _has_extra_from(tail):
    """True if a real Dockerfile FROM appears OUTSIDE any heredoc body (so a Python
    `from x import y` written into a file does not count)."""
    inside = None
    for raw in tail.splitlines():
        if inside is None:
            if re.match(r"\s*FROM\s", raw, re.IGNORECASE):
                return True
            m = re.search(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?", raw)
            if m:
                inside = m.group(1)
        elif raw.strip() == inside:
            inside = None
    return False


# ---------------------------------------------------------------- generator

class RandomGenerator:
    """Generator protocol (seed/propose/fold/state). Floor baseline + seed phase.
    Produces BUILDABLE candidates (build + model-repair loop). No behavioral feedback."""

    def __init__(self, skill, skill_dir, base_image, model="qwen3.6-flash",
                 k=5, temperature=0.9, source="random", build_retries=4,
                 reasoning=True, max_parallel=5, build_timeout=600, outdir=None):
        self.skill = skill
        self.base_image = base_image
        self.outdir = outdir            # if set, proposed NL ideas are persisted here immediately
        self.proposed = []              # all NL ideas proposed so far (before realize+build)
        self.model = model
        self.k = k
        self.temperature = temperature
        self.source = source
        self.build_retries = build_retries
        self.reasoning = reasoning   # model thinking for our gen calls (off = ~3x faster)
        self.max_parallel = max_parallel   # per-item realize+build run concurrently
        self.build_timeout = build_timeout  # seconds per docker build (then skip)
        self.ctx = skill_context(pathlib.Path(skill_dir))
        self.digest = []
        self._buf = []
        self.cost_usd = 0.0
        self.n_batches = 0
        self.n_skipped = 0

    def _make_one(self, item):
        """idea -> realize -> build (+repair). Returns (Candidate|None, cost_usd).
        Pure w.r.t. shared state (safe to run in a thread): unique image tag, no
        mutation of self; cost is returned for the caller to accumulate."""
        cost = 0.0
        try:
            prompt, tail, c = realize(self.ctx, item["task"], item["env"], self.model,
                                      reasoning=self.reasoning)
            cost += c
        except Exception as e:
            print(f"  [realize skip] {item['summary'][:40]!r}: {e}")
            return None, cost
        cid = "cand-" + uuid.uuid4().hex[:12]
        tag = f"skillrace/{cid}:built"
        last_err = ""
        for attempt in range(self.build_retries + 1):
            cf = containerfile_for(self.base_image, tail)
            ok, out = build_image(cf, tag, timeout=self.build_timeout)
            if ok:
                cand = {
                    "candidate_id": cid, "skill": self.skill, "prompt": prompt,
                    "base_image": self.base_image, "containerfile": cf,
                    "built_image": tag,
                    "provenance": {"source": self.source, "parent_run_id": None,
                                   "branch_id": None, "mutation": None,
                                   "summary": item["summary"], "task_nl": item["task"],
                                   "env_nl": item["env"], "build_attempts": attempt + 1},
                }
                return cand, cost
            last_err = out
            if attempt < self.build_retries:
                try:
                    tail, c = repair_tail(self.ctx, tail, out, self.model,
                                          reasoning=self.reasoning)
                    cost += c
                except Exception as e:
                    print(f"  [repair failed] {item['summary'][:40]!r}: {e}")
                    break
        tail_err = (last_err.strip().splitlines() or ["(no output)"])[-1][:160]
        print(f"  [build skip] {item['summary'][:40]!r} after {self.build_retries} "
              f"repairs — last error: {tail_err}")
        return None, cost

    def _refill(self):
        items, presp = propose_batch(self.ctx, self.digest, self.k, self.model,
                                     self.temperature, reasoning=self.reasoning)
        self.cost_usd += presp["cost_usd"]
        self.n_batches += 1
        # Persist + show the NL ideas IMMEDIATELY (one propose call produced them), so
        # they're visible before the slow per-item realize+build phase.
        self.proposed.extend(items)
        for it in items:
            print(f"  proposed idea: {it['summary']}")
        if self.outdir:
            p = pathlib.Path(self.outdir)
            p.mkdir(parents=True, exist_ok=True)
            (p / "ideas.json").write_text(json.dumps(self.proposed, indent=2))
        # realize+build each item CONCURRENTLY (independent; network + subprocess
        # release the GIL). ex.map preserves item order.
        workers = max(1, min(self.max_parallel, len(items)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(self._make_one, items))
        for it, (cand, cost) in zip(items, results):
            self.cost_usd += cost
            if cand is None:
                self.n_skipped += 1
            else:
                self.digest.append(it["summary"])
                self._buf.append(cand)

    def propose(self):
        if not self._buf:
            self._refill()
        return self._buf.pop(0) if self._buf else None

    def drain_buffer(self):
        """Return + clear any candidates that BUILT in the last batch but weren't pulled
        by --n, so their (slow) builds aren't wasted and their images aren't orphaned."""
        rest, self._buf = self._buf, []
        return rest

    def fold(self, candidate, run_dir):
        return  # no behavioral feedback

    def state(self):
        return {"skill": self.skill, "source": self.source, "model": self.model,
                "batches": self.n_batches, "skipped": self.n_skipped,
                "digest": self.digest, "gen_cost_usd": round(self.cost_usd, 6)}


def main():
    ap = argparse.ArgumentParser(description="SkillRACE random/seed generator")
    ap.add_argument("--skill", required=True)
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--base", required=True, help="per-skill base image")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--model", default="qwen3.6-flash")
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--source", default="random", choices=["random", "seed"])
    ap.add_argument("--build-retries", type=int, default=4,
                    help="max model-repair attempts, used ONLY when a build fails")
    ap.add_argument("--max-parallel", type=int, default=5,
                    help="how many items to realize+build concurrently")
    ap.add_argument("--build-timeout", type=int, default=600,
                    help="seconds per docker build before it's treated as a failure")
    ap.add_argument("--no-reasoning", action="store_true",
                    help="disable model thinking for gen calls (~3x faster, lower quality)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    gen = RandomGenerator(args.skill, args.skill_dir, args.base, model=args.model,
                          k=args.k, temperature=args.temperature, source=args.source,
                          build_retries=args.build_retries, reasoning=not args.no_reasoning,
                          max_parallel=args.max_parallel, build_timeout=args.build_timeout,
                          outdir=str(outdir))
    t0 = time.time()
    produced = 0
    for i in range(args.n):
        c = gen.propose()
        if c is None:
            print("generator exhausted")
            break
        (outdir / f"{c['candidate_id']}.json").write_text(json.dumps(c, indent=2))
        produced += 1
        print(f"[{i}] {c['candidate_id']}  {c['provenance']['summary']}")
    # keep any EXTRA candidates that built in the last batch (don't waste slow builds)
    for c in gen.drain_buffer():
        (outdir / f"{c['candidate_id']}.json").write_text(json.dumps(c, indent=2))
        produced += 1
        print(f"[+extra] {c['candidate_id']}  {c['provenance']['summary']} (built in batch, kept)")
    (outdir / "generator_state.json").write_text(json.dumps(gen.state(), indent=2))
    print(f"\nwrote {produced} buildable candidates to {outdir}/ in {time.time()-t0:.1f}s; "
          f"skipped {gen.n_skipped}; gen cost ${gen.cost_usd:.4f}")


if __name__ == "__main__":
    main()
