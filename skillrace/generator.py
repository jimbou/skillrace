"""Random / seed generator (baseline floor + SkillRACE seed phase).

Produces diverse, BUILDABLE `(prompt, env)` test cases for a skill with **no
behavioral feedback** — the floor baseline, and the bootstrap that seeds SkillRACE's
tree. Same component for both roles; only `provenance.source` differs.

Three model-driven steps (all DIRECT to Yunwu so temperature is controllable;
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
import contextlib
import dataclasses
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
from .candidate_policy import validate_generated_tail
from .input_identity import skill_input_tree_hash
from .io_utils import canonical_json_hash
from .parallel_campaign import (
    apply_state_transition,
    load_state_transition,
    publish_state_transition,
    read_state_transition,
)
from .sanity import validate_sanity_spec

TAIL_OPEN = "# >>> SKILLRACE TAIL >>>"
TAIL_CLOSE = "# <<< SKILLRACE TAIL <<<"
SETUP_COMMIT = ('RUN cd /workspace && git add -A && '
                'git commit -q -m "skillrace: test setup" || true')
DEFAULT_BUILD_RETRIES = 4
DEFAULT_BUILD_TIMEOUT = 600
DEFAULT_REALIZATION_TIMEOUT = 300
GENERATION_CALL_TIMEOUT = 180
REALIZER_MAX_TOKENS = 4000
BUILD_REPAIR_MAX_TOKENS = 3000


class GenerationFailure(RuntimeError):
    """One generator-owned proposal attempt failed before an agent could start."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "generation-failure",
        cost_provider_credits: float = 0.0,
    ):
        super().__init__(message)
        self.reason = reason
        self.cost_provider_credits = float(cost_provider_credits or 0.0)


@dataclasses.dataclass(frozen=True)
class RealizationOutcome:
    """Tuple-compatible result that also preserves a failed realization diagnostic."""

    candidate: dict | None
    cost_provider_credits: float
    error: dict | None

    def __iter__(self):
        # Preserve the long-standing ``candidate, cost = ...`` public interface.
        yield self.candidate
        yield self.cost_provider_credits

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
    "need doing. Explore VARIETY of tasks, tools, framework/versions, and structures. "
    "PREFER environments realizable with the TOOLCHAIN the base image already provides "
    "(you are shown its workspace); an idea may add small quick-to-install packages, "
    "but do NOT propose stacks that need a whole new language runtime or heavyweight "
    "framework install unless the skill's purpose demands it. Interesting variation "
    "comes from the STARTING STATE (what is broken and HOW, what is missing, what is "
    "misleading, partial prior work), not from exotic stacks."
)

_RUNTIME_BOUNDARY_PROMPT = (
    " SECURITY BOUNDARY: the tail may set up the project under /workspace, but it "
    "must never use ENTRYPOINT, CMD, ENV, USER, SHELL, HEALTHCHECK, ONBUILD, "
    "STOPSIGNAL, or VOLUME; never set PATH or other runtime interception variables; "
    "and never "
    "read, write, remove, or shadow /skills, /root/.pi, the Pi/Node executables or "
    "packages, shell profiles, global git config, TLS configuration, or a project "
    ".pi directory. The trusted agent runtime and skill mount are immutable."
)

REALIZER_SYS = (
    "You turn ONE test-case idea (a task + an environment, in natural language) into "
    "three concrete artifacts, grounded in the provided base image:\n"
    "  1. PROMPT — the exact instruction to give the coding agent (1-4 sentences).\n"
    "  2. TAIL  — Dockerfile instruction lines that BUILD the described environment "
    "ON TOP OF the base. EVERY shell command must be a Dockerfile instruction "
    "starting with RUN; WORKDIR is also allowed. Use RUN heredocs to create every "
    "project file because the Docker build context contains no project inputs, e.g.:\n"
    "    RUN cat > /workspace/<path> <<'EOF'\n    ...file contents...\n    EOF\n"
    "The base already provides the toolchain, git, the skill, and a /workspace "
    "project; CREATE from scratch anything the environment describes that isn't "
    "standard tooling (files/tests/configs, specific deps/versions, repo state). The "
    "required_paths contract may name only files explicitly listed as existing in "
    "the base or files created by this TAIL. Code snippets in SKILL.md or README files are documentation, "
    "not existing project files. Keep required_paths and created paths exactly aligned. The "
    "TAIL must contain NO `FROM` line, no prose, no code fences.\n"
    "Stay faithful to the skill's purpose (read SKILL.md). The environment must be a "
    "GENUINE, UNSOLVED starting point: infer from the skill what 'unsolved' means and "
    "make the task truly require work — the target must NOT already be "
    "built/fixed/satisfied (e.g. a thing to fix is actually broken and its test "
    "genuinely fails; a thing to build does not yet exist).\n"
    "  3. SANITY — an inspectable mechanical pre-agent contract. required_paths are "
    "absolute paths that the built environment must contain. required_tools are "
    "executable command names: for example, python3 and pip are command names, while "
    "pandas and jsonschema are Python modules and must never appear in required_tools. "
    "task_probe is a non-destructive invocation/collection command "
    "with explicit allowed_exit_codes. unsolved_check is a shell command that exits "
    "0 only if the requested work still remains unsolved, or null when that cannot "
    "be decided mechanically. Every non-null sanity command must actually execute "
    "successfully with valid shell/language syntax in the built initial environment; "
    "in particular, unsolved_check must exit 0 in the initial image. Prefer a short "
    "heredoc script over an invalid one-line try/except. Do not use network access in "
    "any probe.\n"
    'Return ONLY JSON: {"prompt":"...","tail":"...Dockerfile lines...",'
    '"sanity":{"required_paths":["/workspace/..."],"required_tools":["..."],'
    '"task_probe":{"command":"...","allowed_exit_codes":[0]},'
    '"unsolved_check":"shell command"}}. Use the actual JSON value null (not the '
    'string "null") when no mechanical unsolved check is possible.'
    + _RUNTIME_BOUNDARY_PROMPT
)

REPAIR_SYS = (
    "You fix Dockerfile instruction lines (a TAIL applied on top of a base image) "
    "that FAILED to build. Return the COMPLETE REPLACEMENT TAIL, not merely the "
    "changed lines. Make the SMALLEST NECESSARY CORRECTION and preserve every working "
    "file-creation, dependency, repository-state, and setup instruction from the "
    "failing tail; dropping unrelated setup can make a build pass while silently "
    "destroying the requested environment. Given the failing tail and the build "
    "error, output ONLY the corrected instruction lines — no FROM line, no prose, "
    "no code fences."
    + _RUNTIME_BOUNDARY_PROMPT
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

def propose_batch(ctx, digest, k, model, temperature, reasoning=False, skill=None):
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
                tag="generate.propose", skill=skill, retries=1,
                timeout_seconds=GENERATION_CALL_TIMEOUT)
    try:
        items = extract_json(resp["content"])
        if not isinstance(items, list):
            raise ValueError("proposer did not return a JSON array")
        out = [{"summary": it["summary"].strip(), "task": it["task"].strip(),
                "env": it["env"].strip()}
               for it in items if all(key in it for key in ("summary", "task", "env"))]
    except Exception as error:
        raise GenerationFailure(
            f"invalid proposer response: {error}",
            reason="invalid-proposer-response",
            cost_provider_credits=resp.get("cost_provider_credits", 0.0),
        ) from error
    return out, resp


def realize(
    ctx,
    task,
    env,
    model,
    reasoning=False,
    *,
    timeout_seconds=GENERATION_CALL_TIMEOUT,
):
    """Return the shared ``(prompt, tail, sanity, cost)`` realization contract."""
    user = (f"{ctx}\n\nTEST-CASE IDEA:\n- task: {task}\n- environment: {env}\n\n"
            "Return ONLY the requested prompt/tail/sanity JSON object.")
    resp = chat([{"role": "system", "content": REALIZER_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, max_tokens=REALIZER_MAX_TOKENS,
                reasoning=reasoning, retries=1, timeout_seconds=timeout_seconds,
                tag="generate.realize")
    try:
        obj = extract_json(resp["content"])
        prompt, tail = obj["prompt"].strip(), normalize_tail(_strip_fences(obj["tail"]))
        validate_generated_tail(tail)
        sanity = validate_sanity_spec(obj.get("sanity"))
        if _has_extra_from(tail):
            raise ValueError("realized tail contains a FROM instruction")
    except Exception as error:
        raise GenerationFailure(
            f"invalid realizer response: {error}",
            reason="invalid-realization-response",
            cost_provider_credits=resp.get("cost_provider_credits", 0.0),
        ) from error
    return prompt, tail, sanity, float(resp["cost_provider_credits"] or 0.0)


def repair_tail(
    ctx,
    tail,
    build_err,
    model,
    reasoning=False,
    *,
    timeout_seconds=GENERATION_CALL_TIMEOUT,
):
    """Fix a failing tail using the build error. Returns (fixed_tail, cost)."""
    user = (f"{ctx}\n\nThis TAIL (Dockerfile lines on top of the base) FAILED to "
            f"build:\n--- TAIL ---\n{tail}\n--- BUILD ERROR (last lines) ---\n"
            f"{build_err[-1500:]}\n\nOutput the corrected instruction lines only.")
    resp = chat([{"role": "system", "content": REPAIR_SYS},
                 {"role": "user", "content": user}],
                model=model, temperature=0.0, max_tokens=BUILD_REPAIR_MAX_TOKENS,
                reasoning=reasoning, retries=1, timeout_seconds=timeout_seconds,
                tag="generate.build-repair")
    try:
        fixed = normalize_tail(_strip_fences(resp["content"]))
        validate_generated_tail(fixed)
        if _has_extra_from(fixed):
            raise ValueError("repaired tail contains a FROM instruction")
    except Exception as error:
        raise GenerationFailure(
            f"invalid build-repair response: {error}",
            reason="invalid-build-repair-response",
            cost_provider_credits=resp.get("cost_provider_credits", 0.0),
        ) from error
    return fixed, float(resp["cost_provider_credits"] or 0.0)


# ---------------------------------------------------------------- assembly + build

def containerfile_for(base_image, tail):
    validate_generated_tail(tail)
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


def remove_built_image(image):
    """Best-effort removal for a generator-owned image rejected before publication."""
    process = subprocess.run(
        ["docker", "image", "rm", "-f", image],
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = (process.stdout + process.stderr).strip()
    if (
        process.returncode != 0
        and "No such image" not in output
        and "not found" not in output.lower()
    ):
        raise RuntimeError(output[-500:] or f"docker image rm exited {process.returncode}")


def realize_and_build(
    ctx,
    task,
    env,
    model,
    base_image,
    candidate_id,
    *,
    build_retries=DEFAULT_BUILD_RETRIES,
    build_timeout=DEFAULT_BUILD_TIMEOUT,
    realization_timeout=DEFAULT_REALIZATION_TIMEOUT,
    reasoning=False,
    validator=None,
    repair_hint="",
    failed_image_remover=None,
):
    """The one realization/build/repair path shared by all three methods.

    ``validator`` is reserved for an additional method-specific mechanical check
    after a successful build (SkillRACE's target guard).  It does not replace the
    shared sanity gate, which the campaign executes later for every method.
    """
    if realization_timeout <= 0:
        raise ValueError("realization_timeout must be positive")
    deadline = time.monotonic() + realization_timeout
    timeout_error = (
        f"candidate realization timed out after {realization_timeout:g} seconds"
    )

    prompt, tail, sanity, cost = realize(
        ctx,
        task,
        env,
        model,
        reasoning=reasoning,
        timeout_seconds=min(GENERATION_CALL_TIMEOUT, realization_timeout),
    )
    tag = f"skillrace/{candidate_id}:built"
    last_error = None
    built_once = False
    for attempt in range(build_retries + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last_error = timeout_error
            break
        containerfile = containerfile_for(base_image, tail)
        ok, output = build_image(
            containerfile,
            tag,
            timeout=min(build_timeout, remaining),
        )
        built_once = built_once or ok
        if ok and validator is not None:
            try:
                ok, output = validator(tag)
            except Exception as error:
                ok, output = False, f"validator error: {error}"
            if not ok:
                last_error = f"validation failed:\n{str(output)[-1200:]}"
        elif not ok:
            last_error = f"build failed:\n{str(output)[-1200:]}"
        if ok:
            return (
                {
                    "prompt": prompt,
                    "tail": tail,
                    "sanity": sanity,
                    "containerfile": containerfile,
                    "built_image": tag,
                    "build_attempts": attempt + 1,
                },
                cost,
                None,
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last_error = timeout_error
            break
        if attempt >= build_retries:
            break
        try:
            tail, repair_cost = repair_tail(
                ctx,
                tail,
                (last_error or "candidate build failed") + repair_hint,
                model,
                reasoning=reasoning,
                timeout_seconds=min(GENERATION_CALL_TIMEOUT, remaining),
            )
            cost = round(cost + repair_cost, 12)
        except Exception as error:
            cost = round(
                cost + float(getattr(error, "cost_provider_credits", 0.0) or 0.0),
                12,
            )
            last_error = f"repair failed: {error}"
            break
    if built_once:
        try:
            (failed_image_remover or remove_built_image)(tag)
        except Exception as error:
            last_error = f"{last_error or 'candidate rejected'}; cleanup failed: {error}"
    return None, cost, last_error or "candidate could not be built"


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
    (heredoc- AND backslash-continuation-aware), so a tail like
    `cat > f <<'EOF' ... EOF` becomes a valid Dockerfile. Heredoc bodies,
    `\\`-continuation lines of a previous instruction, blanks, comments, and lines
    already starting with a Dockerfile instruction are left untouched."""
    out, inside, continuing = [], None, False
    for raw in tail.splitlines():
        if inside is not None:
            out.append(raw)
            if raw.strip() == inside:
                inside = None
            continue
        if continuing:                 # body of a multi-line instruction: keep verbatim
            out.append(raw)
            continuing = raw.rstrip().endswith("\\")
            continue
        s = raw.strip()
        is_instr = (not s) or s.startswith("#") or any(
            s.upper() == k or s.upper().startswith(k + " ") for k in _DOCKER_INSTR)
        out.append(raw if is_instr else "RUN " + raw)
        m = re.search(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?", raw)
        if m:
            inside = m.group(1)
        elif s and not s.startswith("#"):
            continuing = raw.rstrip().endswith("\\")
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

    def __init__(self, skill, skill_dir, base_image, model="glm-4.5-flash",
                 k=5, temperature=0.9, source="random",
                 build_retries=DEFAULT_BUILD_RETRIES, reasoning=False,
                 max_parallel=5, build_timeout=DEFAULT_BUILD_TIMEOUT, outdir=None,
                 base_image_identity=None):
        self.skill = skill
        self.skill_dir = pathlib.Path(skill_dir)
        self.skill_input_hash = skill_input_tree_hash(self.skill_dir)
        self.base_image = base_image
        self.base_image_identity = base_image_identity or base_image
        # BuildKit cannot use a daemon-local ``sha256:<image-id>`` as FROM; it
        # treats that string as a registry repository. Build from the runnable
        # tag and bind it separately to the campaign-frozen immutable image ID.
        self.build_base_image = self.base_image
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
        self.cost_provider_credits = 0.0
        self.cost_accounting = "known"
        self.n_batches = 0
        self.n_skipped = 0
        self.failure_state = None
        self.folded_attempt_ids = []

    @classmethod
    def for_test(cls, *, source="random"):
        """Construct an offline generator without requiring a real skill checkout."""
        return cls(
            "test-skill",
            "/__skillrace_missing_skill__",
            "skillrace/test-skill:base",
            source=source,
            max_parallel=1,
        )

    def _make_one_detailed(self, item, *, proposal_id=None, provenance=None):
        """Realize one reserved idea without discarding a pre-agent failure reason."""
        cid = proposal_id or ("cand-" + uuid.uuid4().hex[:12])
        if not isinstance(cid, str) or not cid:
            raise ValueError("proposal_id must be a nonempty string")
        try:
            artifact, cost, last_error = realize_and_build(
                self.ctx,
                item["task"],
                item["env"],
                self.model,
                self.build_base_image,
                cid,
                build_retries=self.build_retries,
                build_timeout=self.build_timeout,
                reasoning=self.reasoning,
            )
        except Exception as e:
            print(f"  [realize skip] {item['summary'][:40]!r}: {e}")
            return RealizationOutcome(
                None,
                float(getattr(e, "cost_provider_credits", 0.0) or 0.0),
                {
                    "type": type(e).__name__,
                    "reason": getattr(e, "reason", "realization-failure"),
                    "message": str(e)[:500],
                },
            )
        if artifact is not None:
            cand = {
                "candidate_id": cid,
                "skill": self.skill,
                "prompt": artifact["prompt"],
                "base_image": self.base_image,
                "base_image_identity": self.base_image_identity,
                "containerfile": artifact["containerfile"],
                "built_image": artifact["built_image"],
                "sanity": artifact["sanity"],
                "provenance": {
                    "source": self.source,
                    "requested_base_image": self.base_image,
                    "base_image_identity": self.base_image_identity,
                    "independent_test": self.source == "random",
                    "summary": item["summary"],
                    "task_nl": item["task"],
                    "env_nl": item["env"],
                    "build_attempts": artifact["build_attempts"],
                    **dict(provenance or {}),
                },
            }
            return RealizationOutcome(cand, cost, None)
        tail_err = (str(last_error).strip().splitlines() or ["(no output)"])[-1][:160]
        print(f"  [build skip] {item['summary'][:40]!r} after {self.build_retries} "
              f"repairs — last error: {tail_err}")
        return RealizationOutcome(
            None,
            cost,
            {
                "type": "GenerationFailure",
                "reason": "realization-failure",
                "message": str(last_error or "candidate could not be built")[:500],
            },
        )

    def _make_one(self, item, *, proposal_id=None, provenance=None):
        """Compatibility wrapper returning ``(candidate, provider credits)``."""

        outcome = self._make_one_detailed(
            item, proposal_id=proposal_id, provenance=provenance
        )
        return outcome.candidate, outcome.cost_provider_credits

    def reserve_batch(
        self,
        items,
        reservations,
        *,
        batch_path,
        proposal_cost_provider_credits=0.0,
    ):
        """Bind already-proposed ideas to stable identities before realization.

        The reducer calls this once.  Returned JSON records can then be sent to
        independent workers; no worker needs to mutate generator state.
        """
        items = json.loads(json.dumps(list(items)))
        reservations = list(reservations)
        if len(items) != len(reservations):
            raise ValueError("random items and identity reservations must align")
        records = []
        for item, reservation in zip(items, reservations):
            if not isinstance(item, dict) or not all(
                isinstance(item.get(field), str) and item[field]
                for field in ("summary", "task", "env")
            ):
                raise ValueError("malformed random reservation item")
            candidate_id = getattr(reservation, "candidate_id", None)
            reserved_provenance = getattr(reservation, "provenance", None)
            if not isinstance(candidate_id, str) or reserved_provenance is None:
                raise ValueError("malformed random candidate reservation")
            records.append(
                {
                    "schema": "random-proposal-reservation/1",
                    "candidate_id": candidate_id,
                    "item": item,
                    "provenance": dict(reserved_provenance),
                }
            )
        request = {
            "items": items,
            "reservations": records,
            "proposal_cost_provider_credits": float(
                proposal_cost_provider_credits or 0.0
            ),
        }
        request_hash = canonical_json_hash(request)
        transition = load_state_transition(
            batch_path,
            schema="random-reservation-transition/1",
            request_hash=request_hash,
        )
        if transition is None:
            pre = self.snapshot()
            post = json.loads(json.dumps(pre))
            post["proposed"].extend(json.loads(json.dumps(items)))
            post["counters"]["batches"] += 1
            post["cost_provider_credits"] = round(
                float(post.get("cost_provider_credits", 0.0)) + float(proposal_cost_provider_credits), 12
            )
            post["gen_cost_provider_credits"] = round(post["cost_provider_credits"], 6)
            transition = publish_state_transition(
                batch_path,
                schema="random-reservation-transition/1",
                request_hash=request_hash,
                pre_state=pre,
                post_state=post,
                payload={"reservations": records},
            )
        apply_state_transition(
            self.snapshot(), transition, restore=self.restore
        )
        return tuple(transition["payload"]["reservations"])

    def realize_reservation(self, reservation):
        if (
            not isinstance(reservation, dict)
            or reservation.get("schema") != "random-proposal-reservation/1"
            or not isinstance(reservation.get("item"), dict)
            or not isinstance(reservation.get("provenance"), dict)
        ):
            raise ValueError("malformed random proposal reservation")
        return self._make_one_detailed(
            reservation["item"],
            proposal_id=reservation.get("candidate_id"),
            provenance=reservation["provenance"],
        )

    def complete_reserved_batch(
        self,
        batch_path,
        results,
        *,
        completion_path,
    ):
        batch = read_state_transition(
            batch_path, schema="random-reservation-transition/1"
        )
        reservations = batch["payload"]["reservations"]
        results = json.loads(json.dumps(list(results)))
        expected = [item["candidate_id"] for item in reservations]
        actual = [item.get("candidate_id") for item in results]
        if actual != expected or len(actual) != len(set(actual)):
            raise ValueError("random reservation completion does not match batch order")
        request_hash = canonical_json_hash(
            {"batch_transition_hash": batch["transition_hash"], "results": results}
        )
        transition = load_state_transition(
            completion_path,
            schema="random-reservation-completion/1",
            request_hash=request_hash,
        )
        if transition is None:
            pre = self.snapshot()
            if canonical_json_hash(pre) != batch["post_state_hash"]:
                raise ValueError("random completion state is not the reserved batch state")
            post = json.loads(json.dumps(pre))
            by_id = {item["candidate_id"]: item for item in reservations}
            successes = 0
            failures = []
            total_cost = 0.0
            for result in results:
                total_cost += float(result.get("cost_provider_credits", 0.0))
                if isinstance(result.get("candidate"), dict):
                    successes += 1
                    post["digest"].append(
                        by_id[result["candidate_id"]]["item"]["summary"]
                    )
                else:
                    failures.append(str(result.get("error") or "generation failed"))
            post["counters"]["skipped"] += len(failures)
            post["cost_provider_credits"] = round(float(post["cost_provider_credits"]) + total_cost, 12)
            post["gen_cost_provider_credits"] = round(post["cost_provider_credits"], 6)
            post["failure_state"] = (
                None
                if successes
                else {
                    "type": "GenerationFailure",
                    "reason": "reserved-batch-failure",
                    "message": failures[-1] if failures else "reserved batch failed",
                }
            )
            transition = publish_state_transition(
                completion_path,
                schema="random-reservation-completion/1",
                request_hash=request_hash,
                pre_state=pre,
                post_state=post,
                payload={"results": results},
            )
        apply_state_transition(self.snapshot(), transition, restore=self.restore)
        return transition

    def propose_epoch(
        self, reservations, *, batch_dir, resource_pool=None, **_
    ):
        """Durably reserve and realize one campaign epoch with stable identities."""
        reservations = list(reservations)
        root = pathlib.Path(batch_dir)
        batch_path = root / "reservation.json"
        completion_path = root / "completion.json"
        if batch_path.exists():
            batch_transition = read_state_transition(
                batch_path, schema="random-reservation-transition/1"
            )
            records = batch_transition["payload"]["reservations"]
            expected = [reservation.candidate_id for reservation in reservations]
            if [record["candidate_id"] for record in records] != expected:
                raise ValueError("persisted random epoch reservation identity mismatch")
        else:
            proposal_slot = (
                resource_pool.slots("api")
                if resource_pool is not None
                else contextlib.nullcontext()
            )
            try:
                with proposal_slot:
                    items, response = propose_batch(
                        self.ctx,
                        self.digest,
                        len(reservations),
                        self.model,
                        self.temperature,
                        reasoning=self.reasoning,
                    )
            except Exception as error:
                self.cost_provider_credits = round(
                    self.cost_provider_credits
                    + float(getattr(error, "cost_provider_credits", 0.0) or 0.0),
                    12,
                )
                raise
            if len(items) != len(reservations):
                proposal_cost = float(
                    response.get("cost_provider_credits", 0.0) or 0.0
                )
                self.cost_provider_credits = round(
                    self.cost_provider_credits + proposal_cost, 12
                )
                raise GenerationFailure(
                    "random epoch proposer returned the wrong batch size",
                    reason="proposal-cardinality",
                    cost_provider_credits=proposal_cost,
                )
            records = self.reserve_batch(
                items,
                reservations,
                batch_path=batch_path,
                proposal_cost_provider_credits=response["cost_provider_credits"],
            )
            batch_transition = read_state_transition(
                batch_path, schema="random-reservation-transition/1"
            )
        if completion_path.exists():
            completion = read_state_transition(
                completion_path, schema="random-reservation-completion/1"
            )
            if completion["request_hash"] != canonical_json_hash(
                {
                    "batch_transition_hash": read_state_transition(
                        batch_path, schema="random-reservation-transition/1"
                    )["transition_hash"],
                    "results": completion["payload"]["results"],
                }
            ):
                raise ValueError("persisted random epoch completion request mismatch")
            if canonical_json_hash(self.snapshot()) != completion["post_state_hash"]:
                apply_state_transition(
                    self.snapshot(), batch_transition, restore=self.restore
                )
                apply_state_transition(
                    self.snapshot(), completion, restore=self.restore
                )
            results = completion["payload"]["results"]
        else:
            apply_state_transition(
                self.snapshot(), batch_transition, restore=self.restore
            )
            def realize_one(record):
                worker_slots = (
                    resource_pool.slots("api", "docker")
                    if resource_pool is not None
                    else contextlib.nullcontext()
                )
                try:
                    with worker_slots:
                        outcome = self.realize_reservation(record)
                        candidate, cost = outcome
                    if candidate is None:
                        error = getattr(outcome, "error", None) or {
                            "type": "GenerationFailure",
                            "reason": "realization-failure",
                            "message": "realization/build failed",
                        }
                    else:
                        error = None
                    return candidate, cost, error
                except Exception as error:
                    return None, float(
                        getattr(error, "cost_provider_credits", 0.0) or 0.0
                    ), {
                        "type": type(error).__name__,
                        "reason": getattr(error, "reason", "generation-error"),
                        "message": str(error)[:500],
                    }

            workers = max(1, min(self.max_parallel, len(records) or 1))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                realized = list(executor.map(realize_one, records))
            results = [
                {
                    "candidate_id": record["candidate_id"],
                    "candidate": candidate,
                    "cost_provider_credits": cost,
                    "error": error,
                }
                for record, (candidate, cost, error) in zip(records, realized)
            ]
            self.complete_reserved_batch(
                batch_path,
                results,
                completion_path=completion_path,
            )
        return [
            {
                "candidate": result.get("candidate"),
                "source": self.source,
                "error": result.get("error"),
            }
            for result in results
        ]

    def _refill(self):
        try:
            items, presp = propose_batch(
                self.ctx,
                self.digest,
                self.k,
                self.model,
                self.temperature,
                reasoning=self.reasoning,
            )
        except Exception as error:
            self.cost_provider_credits = round(
                self.cost_provider_credits
                + float(getattr(error, "cost_provider_credits", 0.0) or 0.0),
                12,
            )
            raise
        proposal_cost = presp.get("cost_provider_credits")
        if proposal_cost is None:
            self.cost_accounting = "unknown-nonzero-possible"
        self.cost_provider_credits += float(proposal_cost or 0.0)
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
            results = list(ex.map(self._make_one_detailed, items))
        failures = []
        for it, outcome in zip(items, results):
            cand, cost = outcome
            self.cost_provider_credits += cost
            if cand is None:
                self.n_skipped += 1
                if outcome.error:
                    failures.append(dict(outcome.error))
            else:
                self.digest.append(it["summary"])
                self._buf.append(cand)
        if not self._buf:
            diagnostic = failures[-1] if failures else {}
            self.failure_state = {
                "type": str(diagnostic.get("type") or "GenerationFailure")[:100],
                "reason": str(
                    diagnostic.get("reason") or "no-buildable-candidate"
                )[:100],
                "message": str(
                    diagnostic.get("message")
                    or "random proposal batch produced no buildable candidate"
                )[:500],
            }
            raise GenerationFailure(
                self.failure_state["message"],
                reason=self.failure_state["reason"],
            )
        self.failure_state = None

    def propose(self):
        try:
            if not self._buf:
                self._refill()
            candidate = self._buf.pop(0) if self._buf else None
        except Exception as error:
            self.failure_state = {
                "type": type(error).__name__,
                "reason": getattr(error, "reason", "generation-error"),
                "message": str(error),
            }
            raise
        if candidate is not None:
            self.failure_state = None
        return candidate

    def drain_buffer(self):
        """Return + clear any candidates that BUILT in the last batch but weren't pulled
        by --n, so their (slow) builds aren't wasted and their images aren't orphaned."""
        rest, self._buf = self._buf, []
        return rest

    def fold(self, candidate, run_dir, phase="explore", attempt_id=None):
        if phase != "explore":
            raise ValueError("random has no bootstrap phase")
        if attempt_id is not None and attempt_id not in self.folded_attempt_ids:
            self.folded_attempt_ids.append(attempt_id)
        return  # no behavioral feedback and deliberately no run_dir access

    def snapshot(self):
        """Return the complete JSON-safe proposal state, excluding credentials/context."""
        return {
            "schema": "random-generator/1",
            "source": self.source,
            "skill": self.skill,
            "model": self.model,
            "base_image": self.base_image,
            "base_image_identity": self.base_image_identity,
            "skill_input_hash": self.skill_input_hash,
            "config": {
                "batch_size": self.k,
                "temperature": self.temperature,
                "build_retries": self.build_retries,
                "reasoning": self.reasoning,
                "max_parallel": self.max_parallel,
                "build_timeout": self.build_timeout,
            },
            "digest": json.loads(json.dumps(self.digest)),
            "proposed": json.loads(json.dumps(self.proposed)),
            "buffered_candidates": json.loads(json.dumps(self._buf)),
            "counters": {
                "batches": self.n_batches,
                "skipped": self.n_skipped,
            },
            "cost_provider_credits": self.cost_provider_credits,
            "gen_cost_provider_credits": round(self.cost_provider_credits, 6),
            "cost_accounting": self.cost_accounting,
            "failure_state": json.loads(json.dumps(self.failure_state)),
            "folded_attempt_ids": list(self.folded_attempt_ids),
        }

    def restore(self, snapshot):
        if not isinstance(snapshot, dict) or snapshot.get("schema") != "random-generator/1":
            raise ValueError("unsupported random generator snapshot")
        if snapshot.get("source") != self.source:
            raise ValueError("random generator source mismatch")
        if snapshot.get("skill") != self.skill or snapshot.get("model") != self.model:
            raise ValueError("random generator skill/model mismatch")
        current_skill_hash = skill_input_tree_hash(self.skill_dir)
        if snapshot.get("skill_input_hash") != current_skill_hash:
            raise ValueError("random generator skill input hash mismatch")
        if snapshot.get("base_image_identity") != self.base_image_identity:
            raise ValueError("random generator base-image identity mismatch")
        config = snapshot.get("config")
        expected = {
            "batch_size": self.k,
            "temperature": self.temperature,
            "build_retries": self.build_retries,
            "reasoning": self.reasoning,
            "max_parallel": self.max_parallel,
            "build_timeout": self.build_timeout,
        }
        if config != expected or snapshot.get("base_image") != self.base_image:
            raise ValueError("random generator configuration mismatch")
        counters = snapshot.get("counters")
        if not isinstance(counters, dict):
            raise ValueError("malformed random generator counters")
        self.digest = json.loads(json.dumps(snapshot.get("digest", [])))
        self.proposed = json.loads(json.dumps(snapshot.get("proposed", [])))
        self._buf = json.loads(json.dumps(snapshot.get("buffered_candidates", [])))
        self.n_batches = int(counters["batches"])
        self.n_skipped = int(counters["skipped"])
        self.cost_provider_credits = float(snapshot.get("cost_provider_credits", 0.0))
        self.cost_accounting = snapshot.get("cost_accounting", "known")
        self.failure_state = json.loads(json.dumps(snapshot.get("failure_state")))
        self.folded_attempt_ids = list(snapshot.get("folded_attempt_ids", []))

    def state(self):
        return {"skill": self.skill, "source": self.source, "model": self.model,
                "batches": self.n_batches, "skipped": self.n_skipped,
                "digest": self.digest, "gen_cost_provider_credits": round(self.cost_provider_credits, 6),
                "cost_accounting": self.cost_accounting}


def main():
    ap = argparse.ArgumentParser(description="SkillRACE random/seed generator")
    ap.add_argument("--skill", required=True)
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--base", required=True, help="per-skill base image")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--model", default="glm-4.5-flash")
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--source", default="random", choices=["random", "seed"])
    ap.add_argument("--build-retries", type=int, default=DEFAULT_BUILD_RETRIES,
                    help="max model-repair attempts, used ONLY when a build fails")
    ap.add_argument("--max-parallel", type=int, default=5,
                    help="how many items to realize+build concurrently")
    ap.add_argument("--build-timeout", type=int, default=DEFAULT_BUILD_TIMEOUT,
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
          f"skipped {gen.n_skipped}; gen cost ⚡{gen.cost_provider_credits:.4f}")


if __name__ == "__main__":
    main()
