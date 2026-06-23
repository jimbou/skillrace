# Environment & Container Management

How each test run gets its own isolated, reproducible environment. **This is the
contract between the test synthesizer (which produces an environment) and the
Runner (which executes it).** It supersedes any earlier "base image + bash init
script" description: an environment is now **one Containerfile**.

Related: [runner.md](./design/runner.md) (executes), [guard-synthesizer.md](./design/guard-synthesizer.md)
(produces + validates), [pi-integration.md §4](./pi-integration.md#4-containerization)
(Pi-in-Docker), [data-contracts.md §1](./data-contracts.md#1-candidate--an-input-to-the-runner)
(the `Candidate` schema).

---

## Reference implementation (as built & verified, June 2026)

The design below is **implemented and validated end-to-end** against a real Pi +
CloseAI setup. Concrete artifacts in this repo:

| Path | What it is |
|------|------------|
| `images/pi-base/Dockerfile.pi-base` | Level-1 shared base recipe |
| `images/pi-base/models.closeai.json` | baked CloseAI provider config (key injected at runtime) |
| `images/pi-base/build.sh` | builds `skillrace/pi-base:<version>` + `:latest` |
| `images/pi-base/run_once.sh` | external "Runner" stand-in: run agent on (prompt, skill) → trace + `cost.json` |
| `skills/<skill>/Containerfile.base` | Level-2 per-skill base recipe (`FROM skillrace/pi-base`) |
| `skills/<skill>/seeds/*.Containerfile` | Level-3 per-test recipes (`FROM skillrace/<skill>:base` + tail) |

**Verified facts (these override earlier assumptions from the pi.dev docs):**

- **Agent package:** `@mariozechner/pi-coding-agent@0.62.0`, pinned, on `node:20-bookworm-slim`.
  (npm warns this scope is migrating to `@earendil-works/pi-coding-agent` "going
  forward"; `PI_PKG` is a build-arg so we can switch without editing the Dockerfile.)
- **Provider:** **CloseAI** (OpenAI-compatible proxy) via baked `models.json`. Key is
  the **bare env-var name** `CLOSE_API_KEY` (no `$`), injected at run time with `-e`.
  Traceable models (emit `thinking`/`reasoning_content`): `qwen3.5-flash`,
  `qwen3.6-flash`, `glm-5`. Avoid `gemini-*`/`o4-mini` (no reasoning trace).
- **Run + capture:** `pi --provider closeai --model <m> --print --session
  /logs/session.jsonl --skill /skills/<skill> "<prompt>" </dev/null`. The
  **`--session` JSONL is the trace** (entries tree-linked by `id`/`parentId`); it
  lands in the bind-mounted `/logs`. `</dev/null` prevents a `--print` hang.
- **Cost** is in the trace at `.message.usage.cost.total` per assistant message
  (computed from the per-model `cost` in `models.json`); `run_once.sh` also writes a
  `cost.json` summary artifact.
- **Images built/verified:** `skillrace/pi-base:0.62.0` (430 MB) →
  `skillrace/fix-failing-test:base` (473 MB) → per-test image (built in **~0.16 s**,
  base layers all cache hits). Stored in the local daemon; a portable copy lives at
  `images/pi-base/pi-base-0.62.0.tar` (`docker save`/`docker load`).

Build & run, end to end:

```bash
# Level 1 (once): the shared base  (~11 min first time: npm install of pi; cached after)
images/pi-base/build.sh

# Level 2 (once per skill): the per-skill base
docker build -t skillrace/fix-failing-test:base \
  -f skills/fix-failing-test/Containerfile.base skills/fix-failing-test/

# Level 3 (per test): the per-test image + a run
docker build -t skillrace/run-ftt-seed0:built \
  -f skills/fix-failing-test/seeds/seed0.Containerfile skills/fix-failing-test/
images/pi-base/run_once.sh qwen3.6-flash skills/<skill> out/<run> "<prompt>"
```

The remainder of this page is the design rationale and the contract details.

---

## The decision: an environment is a Containerfile

Each test's initial environment **E₀ is described by a Containerfile** (Dockerfile)
— a complete, self-contained definition of the environment the skill runs in.
**There is no separate "base image + init script" split**; the whole environment,
including the test-specific scenario, lives in one file.

Two consequences make this the right choice:

- **The environment is one artifact.** Storing, reproducing, and shipping E₀ is
  just storing the Containerfile. A bug report ships the **exact Containerfile** that
  triggered it, and anyone can rebuild the precise environment — repros are
  bulletproof.
- **It is maximally expressive.** Any environment you can *build* is a valid test:
  different dependency versions, file layouts, repo states, missing files, dirty git
  state. The mutation space is "any buildable environment," not "what an init script
  can patch onto a fixed image."

This is recorded as **Decision D-ENV-1** (replaces the earlier `env_init` bash
script in the `Candidate` contract — see [Migration](#migration-from-env_init)).

---

## The layering: per-skill base + per-test tail

A full Containerfile rebuild per test would be slow. The fix is **layering with a
strict structure**, so Docker's layer cache makes the shared work free. There are
**three levels** (Decision **D-ENV-3**), each cached independently:

1. **`pi-base`** — Pi + runtime, shared across *all* skills (built once, ever).
2. **per-skill base** — `FROM pi-base` + the task repo/deps/skill (built once per skill).
3. **per-test Containerfile** — `FROM <skill-base>` + the cheap test-specific tail.

### Shared `pi-base` image (built once, all skills)

A single image with Node + Pi (+ git/ripgrep) + a baked CloseAI `models.json`. The
**as-built** recipe is `images/pi-base/Dockerfile.pi-base`:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM node:20-bookworm-slim                        # matches the host-verified runtime
RUN apt-get update \
 && apt-get install -y --no-install-recommends bash ca-certificates git ripgrep \
 && rm -rf /var/lib/apt/lists/*
ARG PI_PKG=@mariozechner/pi-coding-agent@0.62.0   # pinned; build-arg → bump/migrate scope w/o edit
RUN --mount=type=cache,target=/root/.npm \        # cache mount → fast future rebuilds; keeps tarballs out of the layer
    npm install -g --ignore-scripts --loglevel=error "$PI_PKG" && pi --version
COPY models.closeai.json /root/.pi/agent/models.json   # CloseAI provider; key NOT baked
RUN mkdir -p /skills /logs /workspace
WORKDIR /workspace
```

Build with `images/pi-base/build.sh` → tags `skillrace/pi-base:0.62.0` + `:latest`
(430 MB). The model **API key is NOT baked in** — `models.json` names the bare env
var `CLOSE_API_KEY`, injected at run time with `-e`. Because every skill builds
`FROM skillrace/pi-base`, the expensive Pi install (210 packages, ~11 min) happens
**once for the whole project**, not once per skill.

> **Notes from building it:** we use `node:20` (host-verified) and the
> `@mariozechner` scope (current package; pi.dev's `@earendil-works` is the future
> name). **`pi-agent-budget` is deferred** — per-run cost already lands in the trace
> (`.message.usage.cost`), and the extension's native `better-sqlite3` build + TUI
> widget add complexity with little headless value. Add it later only if we want a
> hard cost kill-switch ([Termination](#termination--budget)).

> We deliberately **do not let the synthesizer choose the base image.** A free-choice
> base would bust the layer cache (a full Pi install per distinct base), weaken
> reproducibility, and risk a base where Pi won't install. Instead, each *skill* gets
> its own base (so different skills get different environments), and a *test* that
> needs an unusual tweak can still `RUN` extra installs in its tail.

### Per-skill base image (built once, cached)

For each skill, build **one base image** `FROM skillrace/pi-base` containing
everything stable across all of that skill's test runs:

- the **target repository at its base commit**,
- installed dependencies (language toolchains beyond Node, project deps),
- the **skill files** (`SKILL.md` + scripts), placed where Pi discovers exactly this
  one skill **and no other** ([resolves OQ-5](./pi-integration.md#oq-5---skill-semantics)),
- **`git init` + a single commit at the (buggy) base state** — gives clean
  `git diff`/`--stat` snapshots, a mechanical test-integrity check, and history that
  contains *only* the base (supports the "don't read the fix from git history"
  property), plus
- a `python → python3` symlink so the agent doesn't waste a turn on
  `python: command not found`.

This is the slow part. It happens **~once per skill** (≈20 times total for a
campaign) and is cached. Tag `skillrace/<skill-id>:base`. Network egress for LLM
calls is provided at run time (see [Network](#network-host-network)).

The **as-built** example is `skills/fix-failing-test/Containerfile.base`:

```dockerfile
FROM skillrace/pi-base:0.62.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pytest \
 && rm -rf /var/lib/apt/lists/*
COPY repo/    /workspace/                          # project @ failing-test commit
COPY SKILL.md /skills/fix-failing-test/SKILL.md
RUN set -eux; \
    ln -sf "$(command -v python3)" /usr/local/bin/python; \
    git config --global user.email base@skillrace.local; git config --global user.name skillrace; \
    cd /workspace; printf '__pycache__/\n*.pyc\n' > .gitignore; \
    git init -q -b main; git add -A; git commit -q -m "base: project at failing-test commit"; \
    (python3 -m pytest -q || true)               # record baseline failure; don't fail the build
```

### Per-test Containerfile (generated, fast)

Every generated environment **must**:

1. start with `FROM skillrace/<skill-id>:base` (the pinned base tag/digest), and
2. **only append test-specific layers at the end** — the scenario the mutated guard
   demands (introduce an import error, delete a file, leave uncommitted changes, pin
   a different dependency version).

The **seed** (no mutation) is just the base — `skills/fix-failing-test/seeds/seed0.Containerfile`:

```dockerfile
FROM skillrace/fix-failing-test:base
# >>> SKILLRACE TAIL >>>   (synthesizer writes ONLY this region)
# seed: no scenario change — the repo already ships the failing test.
# <<< SKILLRACE TAIL <<<
```

A **mutated** test changes the failure mode in the tail, e.g.:

```dockerfile
FROM skillrace/fix-failing-test:base
# >>> SKILLRACE TAIL >>>
# turn the assertion failure into an ImportError instead
RUN sed -i 's/^from mathlib import/from mathlib_missing import/' /workspace/test_mathlib.py
# <<< SKILLRACE TAIL <<<
```

Because the only layers that vary are at the **tail**, Docker reuses every cached
layer up to the mutation; the seed build measured **~0.16 s** (all base layers cache
hits) — **seconds, not minutes**.

> **The structure rule is load-bearing.** If a generated Containerfile varies in an
> early layer it busts the cache and pays a full rebuild. Across thousands of runs
> that is the difference between minutes and hours of pure build time. **Enforce the
> rule** by giving the synthesizer a *template* with an untouchable fixed prefix and
> a constrained tail region, and by **rejecting** at validation time any candidate
> whose Containerfile does not begin with the exact pinned `FROM` line or that
> introduces a second `FROM` (no multi-stage that bypasses the base). See
> [Enforcement](#enforcing-the-structure-rule).
>
> **What the rule does NOT constrain:** the *contents* of the tail are unrestricted.
> A tail may pin a specific dependency version, install an extra/heavy package, or
> lay down a particular structure — that's the whole point of "env = a Containerfile"
> (maximally expressive). Such a tail just doesn't share a cached layer, so *that*
> test builds slower; it stays perfectly valid. Putting common things in the base is
> a **speed** optimization, never a correctness restriction.

So: **E₀ = (cached per-skill base) + (generated test-specific tail)** — the same
effect as a fast init script, but expressed as one self-contained, reproducible
artifact.

---

## Per-run flow: validate, then run, in the same container

A single run uses **one ephemeral container instance for two phases**, because the
validator must verify the *exact* environment the agent will then see.

```
RUNNER  (run_case — leaves the container alive; runs NO checks):
  1. BUILD    the per-test Containerfile               (cached base + cheap tail)
  2. START    a LONG-LIVED container, detached          (docker run -d … sleep infinity,
              --network=host, key via -e)
  3. (optional, directed loop only) VALIDATE a mutated guard via `docker exec`, NO
     AGENT — if it fails, destroy + retry, no agent run spent.
  4. RUN      the agent via `docker exec`: baseline `git commit`, then
              pi --print --session /logs/session.jsonl --skill /skills/<skill> "<prompt>"
              under a wall-clock TIMEOUT (no step cap — see Termination).
  5. SNAPSHOT `docker cp` / `git diff` → /logs; capture the trace + cost.
  6. LEAVE    the container RUNNING (record run.json.container) + arm a detached
              TIMEBOMB that removes it after --cleanup-grace if the checker doesn't.

PROPERTY CHECKER  (check_properties — separate command, runs after; owns teardown):
  7. compile NL properties → checks; run STATE checks via `docker exec` IN the live
     container (the exact one the agent left), TRACE checks over the session/diff.
  8. emit verdicts; then `docker rm -f` the container (+ env image).
```

**Why the live container (not a `docker commit` + fresh run):** running the state
checks in the **exact container the agent finished in** is faithful — a `docker
commit` only captures the filesystem, losing `/tmp`, running services, and session
env, so a fresh container could behave differently. **Verified:** the agent's CSV fix
gave `pytest` passing in the live container, and `workspace.diff` showed only the
implementation file changed (test-integrity).

Trace-structural property checks need *episodes*, not the container, so they run
later, after segmentation ([property-checker.md](./design/property-checker.md)).

### How this preserves composability

The Runner and Property Checker are **separate commands** sharing one live container
([data-contracts §0](./data-contracts.md#0-the-universal-component-contract) holds):

- The **Runner (Component 1)** owns build → run → **leave the container alive** (+
  the timebomb). It runs no checks and does no `docker commit`.
- The **Property Checker (Component 6)** runs **after**, `exec`s its state checks
  into `run.json.container`, emits verdicts, and **owns teardown** (`docker rm -f`).
- The (optional) **Validator (Component 5c)** — for the directed loop only — `exec`s
  a guard check before the agent; if it fails, no agent run is spent.

The hand-off is a **file contract** (the Runner writes `run.json` with the live
`container` name; the checker reads it). The timebomb bounds the coupling: if the
checker never runs, the container is reclaimed anyway.

---

## Isolation by destruction, not reset

A coding agent with shell access **will** mutate its environment (git state,
installed packages, temp files). The container is **destroyed and rebuilt for every
run — never reused** — so no state leaks between runs.

- **No host filesystem mount.** The target repo lives *inside* the image (baked into
  the base), so the host working directory is **not** mounted. Nothing on the host
  changes; the environment is fully captured by the Containerfile + base digest.
- This is what makes the **optional k-fold reproduction grading** (Component 6;
  configurable, default off) and the **"same input → same behavior"** claims
  meaningful: each regrade run starts from an identical, freshly-built container with
  zero carryover.

---

## Network: host network

The run container is started with **`--network=host`**, giving it the host's network
stack directly. Recorded as **Decision D-ENV-2**.

- **Why:** Pi must reach the model API to drive the agent under test (LLM calls need
  egress). Host networking is the simplest, most reliable way to grant that egress
  without per-container network plumbing.
- **API key at run time, never in the image.** The model API key is injected at
  `docker run` via `-e ANTHROPIC_API_KEY` (etc.) — **never** written into the
  Containerfile. So a shipped Containerfile (in a bug report) carries no secret, and
  base images are safe to cache/share.
- **Security tradeoff (noted, accepted):** `--network=host` shares the host network
  namespace, reducing isolation versus a bridged/egress-filtered network. Acceptable
  for a trusted testing harness running on dedicated infrastructure; if tighter
  isolation is later required, swap to a bridge network with an egress allowlist to
  the model endpoint — a Runner-config change that does **not** affect any contract.

Example `docker run` (per test), wrapped in a wall-clock timeout:

```bash
timeout --signal=KILL 900 \
  docker run --rm --network=host \
    -e ANTHROPIC_API_KEY \
    --name run-<run_id> \
    skillrace/run-<run_id>:built \
    pi --mode json --skill /skills/fix-failing-test "<prompt>"
# on timeout, also `docker kill run-<run_id>` to tear the container down
```

(No `-v "$PWD:/workspace"` mount — the repo is in the image. No `-e stepcap.ts`
extension — we cap by time, not steps; see Termination.)

---

## Termination & budget

The tex mandates a hard step/turn cap; we **replace it with a wall-clock timeout**
(Decision **D-RUN-1**), which is simpler, needs no custom Pi extension, and resolves
[OQ-2](./pi-integration.md#oq-2-step-cap).

- **Primary: wall-clock timeout** (`runner.wall_clock_cap_s`, default 900s). On
  expiry the container is killed; `run.json.termination.reason="timeout"`; whatever
  steps were captured are normalized into a (partial) trace.
- **Optional backstop: token/cost hard cap** via the `pi-agent-budget` extension
  baked into `pi-base` — set a hard per-run limit so a fast-looping agent can't burn
  unbounded tokens within the timeout. Trips `termination.reason="token_budget"`.
- **Pathological repetition is not capped — it is *detected*.** A rapid-fire loop
  that finishes within the timeout is caught by the **process-hygiene property** "no
  pathological repetition" ([property-checker.md](./design/property-checker.md)),
  i.e. reported as a *bug* rather than hidden by a cap. (Re-introduce a turn cap only
  if runaway loops prove to waste meaningful budget in practice.)

Budget tracking for the whole campaign uses the same `pi-agent-budget` data
([pi-integration §6 OQ-6](./pi-integration.md#oq-6-usagecost-for-budgeting)).

---

## Enforcing the structure rule

The synthesizer is handed a **template**; the Runner/Validator **enforce** it before
any build is trusted:

| Rule | Enforced by |
|------|-------------|
| First non-comment line is **exactly** `FROM skillrace/<skill-id>:base@sha256:<pinned-digest>` | string check on the Containerfile; pinned digest comes from the skill's base build, not the candidate |
| **No second `FROM`** (no multi-stage bypass) | reject if `>1` `FROM` instruction |
| Tail confined to the writable region between the template markers | the synthesizer only ever writes between `# >>> SKILLRACE TAIL >>>` and `# <<< SKILLRACE TAIL <<<` |
| No secrets / no `ARG`-injected credentials in the file | scan for env/secret patterns; the API key is run-time `-e` only |
| Build is hermetic enough to cache | the base provides deps; the tail should avoid unpinned network installs that defeat reproducibility (flagged, not hard-failed) |

A candidate that violates a hard rule is **rejected at validation** with
`rejected_reason` set — it never reaches a build or an agent run.

---

## Migration from `env_init`

Earlier drafts modeled E₀ as `Candidate.env_init` (a bash script run on a fixed base
image). That field is **replaced**:

| Old | New |
|-----|-----|
| `Candidate.env_init: string` (bash init script) | `Candidate.containerfile: string` (full Containerfile text — the shippable artifact) + `Candidate.base_image: string` (pinned `<skill-id>:base@sha256:…` the prefix must reference) |
| `run.json.input.env_init` | `run.json.input.containerfile` + `run.json.input.base_image` |
| BugReport repro by start-image tag | BugReport ships `repro.containerfile` (+ `repro.base_image`) — rebuildable anywhere |
| Validator/Runner each build their own container | **One build** per run; validate + run share the instance (above) |

All schemas in [data-contracts.md](./data-contracts.md) and the manifest in
[trace-format.md](./trace-format.md) reflect the new fields.

---

## How to test this in isolation

- **Cache discipline** (`tests/fixtures/env/cache/`): build a base; build two
  per-test Containerfiles whose tails differ; assert all pre-tail layers are cache
  hits (parse `docker build` output) and only the tail layers rebuild.
- **Structure enforcement** (`tests/fixtures/env/structure/`): feed Containerfiles
  that (a) omit the pinned `FROM`, (b) add a second `FROM`, (c) write outside the
  tail markers, (d) embed a secret → assert each is **rejected** with the right
  `rejected_reason`, with **no build of the agent and no agent run**.
- **Same-container gate** (`tests/fixtures/env/gate/`): a candidate whose guard the
  setup does **not** satisfy → assert the agent entry point is never called and the
  container is destroyed; a candidate whose guard holds → assert the agent run
  (phase 4) **and** the state-based property checks (phase 5) execute in the **same**
  container id the validator checked (phase 3).
- **Snapshot-out** (`tests/fixtures/env/snapshot/`): after a (stub) agent edits/creates
  files, assert `<run>/workspace_snapshot/` contains the git diff + changed files
  **before** the container is destroyed, and that **no `docker commit`** was issued.
- **Timeout** (`tests/fixtures/env/timeout/`): a stub agent that exceeds
  `wall_clock_cap_s` → container killed, `termination.reason="timeout"`, partial
  trace still normalized and well-formed.
- **Isolation by destruction** (`tests/fixtures/env/isolation/`): run a candidate
  whose (stub) agent writes a file and dirties git; run it again; assert the second
  run starts from a clean state (the write/dirt is gone) and the host cwd is
  unchanged.
- **Host network** (`tests/fixtures/env/network/`): assert the run command includes
  `--network=host` and that no API key appears in the built image
  (`docker history` / image inspect shows no secret layer).
