# Pi Integration

How SkillRACE uses the [Pi agent framework](https://pi.dev/docs/latest), grounded
in the fetched documentation (June 2026). Every claim cites the page it came from.
Where the design depends on something the docs do **not** settle, it is recorded
as an **Open Question (OQ)** with the chosen fallback — never guessed silently.

> Citations are to pages under `https://pi.dev/docs/latest/…`. Some details were
> read through a documentation-summarization fetch; anything that is load-bearing
> **and** not verbatim in the docs is called out as an OQ so it gets confirmed
> against a real Pi install during Milestone 0.

> **Verified against real Yunwu runs (July 2026).** The agent package is
> **`@mariozechner/pi-coding-agent@0.73.1`** (pinned; pi.dev's `@earendil-works`
> scope is the *future* name per an npm deprecation notice) on **`node:20`**. The
> provider used is **Yunwu** (OpenAI-compatible). The trace is captured via
> **`--print --session <file>`** (the `--session` JSONL is the trace; `--mode json`
> also exists). See [environments.md → Reference implementation](./environments.md#reference-implementation-as-built--verified-june-2026).
> OQ-1/2/4/5/6 are now resolved (§6).

---

## 1. The two isolated places Pi is used

Pi is used in two deliberately separate roles:

1. the Runner executes the agent under test with the selected skill; and
2. the optional SkillRACE patch backend performs one patch-only repair after a
   definite failure, before a separately launched exact replay.

This is a deliberate architectural decision (**D-PI-1**) with a big payoff:

- The agent under test is "whatever agent the skill targets" (tex §1) — that is
  precisely a Pi coding agent driven by a `SKILL.md`. The Runner orchestrates it.
- Every other *cheap-model judgment step* (segmentation, summarization, merge decisions,
  guard extraction, SBE compilation) is a **direct provider API call** (e.g. the
  Anthropic Messages API), **not** a Pi run. Those steps need temperature 0,
  deterministic caching, and tight prompt control — all of which the design owns
  directly when it bypasses Pi.

Consequence for the tex's determinism story: the temperature-0 + caching
guarantees for Components 2–6 are fully under SkillRACE's control because those
calls do not go through Pi. Only the *agent-under-test* run depends on Pi's
sampling controls — see [OQ-1](#oq-1-temperature).

---

## 2. How the Runner drives Pi

### 2.1 Invocation: headless, in a container

The Runner shells out to the Pi CLI **inside the per-run Docker container**. The
**as-built / verified** invocation:

```bash
pi --provider yunwu --model glm-4.5-flash --print \
   --session /logs/session.jsonl --skill /skills/<name> "<prompt>" </dev/null
```

- `--print` runs non-interactively and exits (the prompt is the positional arg).
  **`</dev/null` is required** — `--print` hangs waiting on stdin otherwise.
- **`--session /logs/session.jsonl` writes the session to a file → that file is the
  trace** (durable, in the bind-mounted `/logs`). This is our primary capture path.
- `--skill <path>` loads the skill under test ([skills](https://pi.dev/docs/latest/skills));
  in practice the skill is **baked into the per-skill base image** so this path is
  in-image and exactly one skill is discoverable.
- `--provider`/`--model` select the model; here Yunwu + a traceable model.
- `--mode json` (text/json/rpc) also exists ([json](https://pi.dev/docs/latest/json))
  if we want the event stream on stdout for live monitoring; `--session` is enough
  for the durable trace.

**Why CLI-in-container for the agent under test** (**D-PI-2**): the SDK
(`createAgentSession`, [sdk](https://pi.dev/docs/latest/sdk)) runs in-process,
which is harder to sandbox and resource-cap per run. A child `pi` process inside
Docker gives clean isolation, a kill switch (wall-clock cap), and a captured
stdout stream. The SDK remains a viable alternative if in-process control is later
needed. The much narrower patch-only repair role does use the SDK inside its own
fresh container, as described below; that does not change the agent-under-test
execution path.

### 2.5 Guided SDK use for patch-only repair

SkillRACE's optional Pi patcher mounts a writable copy of the original skill and one
read-only `repair-context.json`. The initial prompt contains only the repair objective
and the two paths. Pi must read the complete `SKILL.md` and repair context before it can
mutate anything. The SDK enables only `read`, `grep`, `edit`, and `write`; a reviewed
inline policy extension limits reads to the two mounted inputs, permits exactly one
mutation of `/workspace/SKILL.md`, and blocks all later tool calls. Before both direct
reads complete, it blocks `grep` and duplicate reads. Once both mandatory reads
complete, it dynamically disables `read` and `grep`, leaving only `edit` and `write`.
Skills, project extensions, prompt templates, themes, and context-file discovery are
disabled.

The patcher has no bash or other execution tool, no checker or confirmation image, and
no hidden replay information. It therefore cannot rerun the failed task while patching.
Pi is asked to stop after the edit, an SDK abort boundary applies after ten turns, and
the outer container retains the independent 300-second timeout. Session/event data is
used transiently to extract input/output/cache tokens, turns, tool calls, blocked calls,
remaining required reads, provider credits and failure location, then deleted. Exact
replay remains a later orchestrator operation.

### 2.2 Two capture paths (one durable, one streaming)

1. **Streaming**: read the child's stdout line by line (split on `\n` **only** —
   the [rpc](https://pi.dev/docs/latest/rpc) page warns Node `readline` is
   non-compliant because it also splits on `U+2028`/`U+2029`; the same applies to
   any JSONL reader). This gives live events for the step cap and progress.
2. **Durable**: Pi also persists the session to a JSONL file under
   `~/.pi/agent/sessions/…` ([sessions](https://pi.dev/docs/latest/sessions),
   [session-format](https://pi.dev/docs/latest/session-format)). The Runner copies
   that file out of the container into `raw/session.jsonl`.

Both are saved to `raw/` for audit; the **frozen trace** is normalized from them
(§3). The `agent_end` event "includes all messages" ([json](https://pi.dev/docs/latest/json),
[rpc](https://pi.dev/docs/latest/rpc)), so in the common case the entire trace can
be reconstructed from that single terminal event, with the session file as backup.

### 2.3 The event/entry → frozen-step mapping

From [session-format](https://pi.dev/docs/latest/session-format) and
[json](https://pi.dev/docs/latest/json):

| Pi source | Frozen step field |
|-----------|-------------------|
| `AssistantMessage.content[]` `{type:"thinking", thinking}` + `{type:"text", text}` | `reasoning` (D-TRACE-1) |
| `AssistantMessage.content[]` `{type:"toolCall", id, name, arguments}` | `tool`, `args`; `id`→`meta.tool_call_id` |
| `ToolResultMessage{toolCallId, content[], isError}` | matched by `toolCallId`; text→`observation`; `isError`→`meta.is_error` |
| `AssistantMessage.{model,stopReason,usage}` | `meta.{stop_reason,usage}`; `model`→`run.json.model` |
| `type:"compaction"` / `CompactionSummaryMessage` | dropped; `meta.compaction_before` flag (D-TRACE-4) |
| `UserMessage` (initial) | not a step; → `run.json.input.prompt` |

The full normalization (multi-tool turns, terminal step, truncation) is specified
in [trace-format.md §5](./trace-format.md#5-normalization-rules-pi-session--frozen-trace).
The mapping is exercised by replaying recorded `raw/session.jsonl` fixtures through
the normalizer with **no Pi process running** (see [runner.md](./design/runner.md)).

### 2.4 Termination (timeout, not a step cap)

The tex mandates a "hard step/turn cap per run," and the SDK docs confirm Pi "doesn't
expose iteration caps or step limits" ([sdk](https://pi.dev/docs/latest/sdk)). Rather
than work around that with a custom extension, SkillRACE **replaces the step cap with
a wall-clock timeout** (Decision **D-RUN-1**), which needs no Pi-side mechanism and
**resolves [OQ-2](#oq-2-step-cap)**:

1. **Wall-clock timeout** (primary): the Runner wraps the `docker run` in `timeout
   --signal=KILL <wall_clock_cap_s>` and `docker kill`s the container on expiry —
   independent of Pi, so a hung tool can't run forever.
2. **No hidden token/cost stop:** the host records usage and provider credits, while
   the wall-clock boundary is the only hard stop in the frozen protocol.

Recorded in `run.json.runner.wall_clock_cap_s` and `run.json.termination`. **No custom
TypeScript extension is needed for the agent under test** — combined with reasoning
being captured natively (§2.3), this keeps the tested execution path extension-free.
Pathological fast
loops that finish within the timeout are caught downstream as a *property* violation,
not hidden by a cap ([environments.md](./environments.md#termination--budget)).

---

## 3. Tooling and skills

- **Tools.** The agent under test uses Pi's own toolset (`read`, `bash`, `write`,
  `edit`, …). SkillRACE registers **no** custom tool or extension into that tested
  agent. The separate patch-only process uses a policy extension, but the extension
  adds no semantic repair capability; it only narrows paths, tool ordering and the
  one-mutation boundary.
- **Skills.** A skill is a directory with `SKILL.md` + YAML frontmatter
  (`name`, `description`, optional `allowed-tools`, etc.) plus scripts/assets
  ([skills](https://pi.dev/docs/latest/skills)). The Runner pins the skill by
  hashing `SKILL.md` (+ scripts) into `run.json.skill.sha256` so every trace/bug is
  attributable to an exact skill revision.

---

## 4. Containerization

Per [containerization](https://pi.dev/docs/latest/containerization), Pi documents a
plain-Docker pattern: a `Dockerfile.pi` from `node:24-bookworm-slim` that installs
git/ripgrep and Pi via npm, then:

```bash
docker run --rm -e ANTHROPIC_API_KEY -v "$PWD:/workspace" -v pi-agent-home:/root/.pi/agent pi-sandbox
```

SkillRACE's Runner follows this pattern, but the **environment is a Containerfile**,
not a base-image-plus-init-script — the full design is in
[environments.md](./environments.md). In Pi terms:

- A **per-skill base image** (built once, cached) bakes in the OS/runtime, the target
  repo at its base commit, dependencies, **Pi installed**, and the **skill files** so
  `--skill` resolves to exactly this one skill ([resolves OQ-5](#oq-5---skill-semantics)).
  The model **API key is NOT baked in** — it is injected at run time.
- Each run builds the candidate's **Containerfile** (`FROM <skill-base>` + a cheap
  test-specific tail) — base layers are cache hits, so the per-test build is seconds.
- The container is started **`--network=host`** so Pi can reach the model API
  ([D-ENV-2](./environments.md#network-host-network)); the key is passed with `-e`.
  **No `-v "$PWD:/workspace"` mount** — the repo is inside the image, so nothing on
  the host changes and the environment is fully captured by the Containerfile.
- The Runner runs the agent via `docker exec` in a long-lived container
  (`pi --print --session … --skill /skills/<name> "<prompt>"`) under a wall-clock
  `timeout` (no step-cap extension) ([per-run flow](./environments.md#per-run-flow-validate-then-run-in-the-same-container)).
- After the agent exits, the Runner `docker cp`s a `workspace_snapshot/` out (and
  `raw/session.jsonl`), then **leaves the container running** (no `docker commit`) and
  arms a detached timebomb to reclaim it after a grace period.
- The **separate Property Checker** later stages trace/diff evidence in that live
  container, snapshots its final filesystem once, and runs each check in a fresh
  networkless child ([property-checker.md](./property-checker.md)), then destroys the
  original container. Post-campaign confirmation rebuilds the representative
  `run.json.input.containerfile` once outside the search budget.

The Gondolin micro-VM extension mentioned on the same page is **not** used in v1
(plain Docker with host networking is sufficient and simpler to reproduce).

---

## 5. What SkillRACE does NOT use from Pi

- **No SDK embedding for the agent under test** in v1 (D-PI-2) — it remains a CLI
  child process. The isolated patch-only backend is the documented exception.
- **No custom agent tools** injected into the agent under test (§3).
- **No Pi-side model judgments** — segmentation/summarization/merge/guards/SBE all
  call the provider API directly, not Pi (D-PI-1).
- **No reliance on Pi's branching/labels/compaction-summaries as semantic signal** —
  these are linearized/dropped by the normalizer ([trace-format.md §5](./trace-format.md#5-normalization-rules-pi-session--frozen-trace)).

---

## 6. Open Questions

Each OQ states: what the design needs, what the docs say, what is unconfirmed, and
the fallback. OQs are revisited in **Milestone 0** (a smoke test against a real Pi
install) before the Runner is declared done.

### OQ-1 (temperature)
**Need:** agent-under-test at temperature 0 (tex §3); deterministic-ish runs.
**Docs:** temperature is **not exposed** in the SDK ([sdk](https://pi.dev/docs/latest/sdk):
"The SDK doesn't expose temperature directly. Instead, use `thinkingLevel`"), nor
in [providers](https://pi.dev/docs/latest/providers), nor as a `models.json` field
([models](https://pi.dev/docs/latest/models): sampling params "appear to be
runtime/request-level settings rather than static model configuration").
**Unconfirmed:** whether a `models.json` `compat` override or a CLI flag can pin
temperature; whether Pi defaults Anthropic temperature to 1.0.
**Fallback / RESOLVED (by design, confirmed):** temperature is not settable for the
agent under test, and that is **accepted** — we run the agent at Pi's default and
record `run.json.model.temperature = null`. The tex already treats run determinism as
an *empirically reported* property ("Determinism, honestly" — build stability under a
reseed), so non-zero agent temperature degrades gracefully into a measured stability
number. **Crucially this does not affect Components 2–6**, which bypass Pi and set
temperature 0 on every judgment call directly — which is where the determinism/caching
guarantees actually live. (Optionally still try a `models.json` `compat` override to
pin the agent to 0; treated as a nice-to-have, not a requirement.)

### OQ-2 (step cap)
**Need:** a hard per-run step/turn cap (tex §3).
**Docs:** not exposed by SDK ([sdk](https://pi.dev/docs/latest/sdk)).
**RESOLVED (by design):** we **drop the step cap** and bound runs with a wall-clock
`timeout` (Decision **D-RUN-1**, §2.4). No custom extension and no dependence on an
undocumented extension-`abort`. Pathological
fast loops within the timeout are caught as a *property* violation rather than hidden
by a cap.

### OQ-3 (session linearization)
**Need:** a single linear step sequence from a possibly tree-structured session.
**Docs:** sessions are a tree; "current position is the active leaf"; context is
the path from leaf to root ([session-format](https://pi.dev/docs/latest/session-format),
[sessions](https://pi.dev/docs/latest/sessions)).
**Unconfirmed:** whether a headless single-prompt run ever branches on its own
(it shouldn't, absent steering).
**Fallback:** the normalizer always takes the leaf→root path and ignores
abandoned branches; it asserts the path is a simple chain and records any branching
in `termination.detail`. A fixture with an artificially branched session tests this
path.

### OQ-4 (thinking capture)
**Need:** rich `reasoning` per step — it is the boundary/guard signal (tex §2, §5).
**Docs:** assistant content carries `{type:"thinking"}` blocks and the stream emits
`thinking_start/delta/end` ([session-format](https://pi.dev/docs/latest/session-format),
[rpc](https://pi.dev/docs/latest/rpc)); thinking is controlled by `thinkingLevel`
([sdk](https://pi.dev/docs/latest/sdk)).
**RESOLVED (verified):** Yunwu's `glm-4.5-flash` and `deepseek-v4-flash` emit
reasoning that Pi 0.73.1 records as `{type:"thinking"}` blocks. The archived probes
exercise multiple tool/result turns and final responses. A model that redacts thinking
is outside the two frozen headline tracks.

### OQ-5 (`--skill` semantics)
**Need:** load exactly the skill under test, nothing else, headlessly.
**Docs:** `--skill <path>` is listed as a discovery location ([skills](https://pi.dev/docs/latest/skills));
skills are otherwise auto-discovered from many locations.
**RESOLVED (verified):** the **per-skill base image bakes in exactly one skill**
([environments.md](./environments.md#per-skill-base-image-built-once-cached)), so the
discoverable set is the one skill regardless of `--skill`'s add-vs-restrict semantics.
In the `fix-failing-test` run the agent **read `/skills/fix-failing-test/SKILL.md`**
and followed it (ran pytest → read → fixed code → re-ran), confirming the skill both
loads and steers behavior headlessly.

### OQ-6 (usage/cost for budgeting)
**Need:** per-run token/cost accounting to manage the ~100–150 runs/skill budget.
**Docs:** `AssistantMessage.usage` carries input/output/cache tokens and a `cost`
breakdown ([session-format](https://pi.dev/docs/latest/session-format)).
**RESOLVED (implemented):** sum the immutable session trace's input, output, and cache
usage on the host and price it with the dated Yunwu custom-credit policy. Catalog costs
are deliberately omitted, so Pi's internal zero does not masquerade as free usage. The
receipt records provider credits and leaves USD null.

---

## 7. Milestone 0 smoke test (resolves the OQs)

Before the Runner is "done", run this against a real Pi install and record results
in this file:

1. `pi --mode json --skill ./skills/hello "say hi and run `echo ok`"` → confirm the
   event stream contains `agent_start`, ≥1 `tool_execution_*`, `agent_end` with a
   messages array; confirm a session file appears under `~/.pi/agent/sessions/`.
2. Inspect one `AssistantMessage` for `thinking` content at `thinkingLevel:"medium"`
   (OQ-4 — the only OQ still affecting correctness).
3. Confirm host-side usage accounting and that wall-clock timeout plus forced container
   cleanup tears a long run down cleanly (OQ-2/OQ-6).
4. Confirm `--skill` in a clean container (skill baked into the base) exposes exactly
   one skill (OQ-5).
5. Force a session branch (via steering) and confirm leaf→root linearization (OQ-3).
6. (Optional) attempt to pin agent temperature via `models.json` `compat` (OQ-1,
   nice-to-have).

Each result flips an OQ from "open" to "resolved (behavior X)" with the date.
OQ-1/2/6 are already **resolved by design**; the smoke test only confirms mechanics.
