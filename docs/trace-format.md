# The Frozen Trace Format

> **Status: FROZEN.** This is the single contract between the Runner (Component 1)
> and every downstream component. It is defined once, early, and does not change
> without a version bump. Downstream components (segmenter, summarizer, tree
> builder, guard extractor, property checker, all baselines) consume **only** this
> format — never Pi's native session objects, never the live agent, never each
> other's internals.
>
> Source of truth in the design: `skillrace-implementation.tex` §3 ("The runner
> and trace format"). Pi-side grounding: [`docs/pi-integration.md`](./pi-integration.md).

---

## 1. Why a frozen format exists

The agent under test runs on the [Pi framework](https://pi.dev/docs/latest). Pi
produces a rich, **tree-structured** session (entries linked by `id`/`parentId`,
with thinking/text/toolCall content blocks, tool-result messages, compaction
entries, branch summaries, usage accounting, etc. — see
[Pi session-format](https://pi.dev/docs/latest/session-format)). That richness is
Pi's internal contract and is subject to change across Pi versions.

SkillRACE does **not** let that richness leak downstream. The Runner **projects**
each Pi session into a flat, linear, minimal JSONL trace whose schema is frozen
here. Everything downstream is written against this projection. If Pi changes its
session schema, only the Runner's normalizer changes; the rest of the system is
untouched.

This is the literal meaning of the tex's "frozen early; everything downstream
consumes only this."

---

## 2. On-disk layout

A campaign is laid out **by method, then skill, then numbered run**:

```
<out>/
  <method>/                       # skillrace | random | greybox
    <skill>/                      # one subfolder per skill under test
      tree.json                   # the behavior tree for the whole campaign (this method+skill)
      frontier.json               # current branch frontier
      coverage.json               # coverage report (explored vs unexplored)
      bugs/                       # one BugReport per file
      000/  001/  002/  …         # one numbered subfolder PER RUN, in order
```

One agent run is one numbered **run directory** `<out>/<method>/<skill>/<NNN>/`:

```
<NNN>/
  run.json              # manifest: identity, input, environment, termination
  trace.jsonl           # the frozen trace — one JSON object per line, one per step
  segmentation.json     # Component 2 output
  episodes.json         # Component 3 join (Episode[])
  verdicts.json         # Component 6 property verdicts for this run
  validation.json       # the ValidationReport (gate) for this run, if synthesized
  workspace_snapshot/   # git diff + changed/created files copied out before destroy (§6)
  raw/                  # untouched Pi artifacts kept for debugging only
    events.jsonl        #   raw `pi --mode json` stdout stream
    session.jsonl       #   raw Pi session file copied out of the container
```

- `trace.jsonl` and `run.json` are the **contract**. Downstream components read the
  per-run artifacts they declare in [data-contracts.md](./data-contracts.md); the
  tree/frontier/coverage live at the skill level.
- `<NNN>` is the run's ordinal within this method+skill (zero-padded). `run.json`
  also carries a globally-unique `run_id`.
- `raw/` is **not** part of the contract. It exists so a human can audit the
  normalization. No component may read `raw/`. (Enforced in review, not in code:
  if a component needs something from `raw/`, that something is missing from the
  frozen schema and the schema must be extended — see §8.)

---

## 3. The frozen step schema (`trace.jsonl`)

Each line of `trace.jsonl` is one **step**. A step is exactly: *the agent's
reasoning, the single tool call it then made, and the raw output of that call.*

### 3.1 Core fields (the contract)

These five fields are the tex's frozen schema verbatim. Downstream components MUST
be implementable using only these.

| Field         | Type             | Meaning |
|---------------|------------------|---------|
| `step`        | `int`            | 0-based, strictly increasing by 1, contiguous, no gaps. The stable index used everywhere downstream (segmenter evidence pointers, episode step-ranges, etc.). |
| `reasoning`   | `string`         | The assistant's stated rationale **before** this tool call. Empty string `""` if the agent emitted no reasoning before this call (see Decision D-TRACE-2). |
| `tool`        | `string \| null` | The tool name (`"bash"`, `"read"`, `"write"`, `"edit"`, …). `null` only for a **terminal step** (a final assistant turn with no tool call — see Decision D-TRACE-3). |
| `args`        | `object`         | The tool-call arguments as a JSON object. `{}` when `tool` is `null`. |
| `observation` | `string \| null` | The **raw** tool output (stdout/stderr/return text), verbatim from the tool result. `null` when `tool` is `null`. This is the field the summarizer reads outcomes from — never `reasoning`. |

### 3.2 Provenance field (`meta`) — optional, non-contract

The Runner also attaches a `meta` object to each step carrying Pi-derived
provenance. **`meta` is not part of the frozen contract.** Components MAY read it
as a convenience/tiebreaker, but no component may *require* it; anything load-bearing
must be expressible from the core five fields. (Rationale: keeps the contract small
and keeps Pi's schema from leaking into component logic, while preserving useful
signal for debugging and optional grounding.)

| `meta` field      | Type              | Source in Pi |
|-------------------|-------------------|--------------|
| `tool_call_id`    | `string`          | `ToolCall.id` / `ToolResultMessage.toolCallId` ([session-format](https://pi.dev/docs/latest/session-format)) |
| `is_error`        | `bool`            | `ToolResultMessage.isError` |
| `stop_reason`     | `string \| null`  | `AssistantMessage.stopReason` (`"stop"`,`"length"`,`"toolUse"`,`"error"`,`"aborted"`) |
| `turn`            | `int`             | Index of the Pi turn this step came from (multiple steps can share one turn) |
| `ts`              | `string` (ISO8601)| Entry timestamp |
| `usage`           | `object \| null`  | Token/cost usage from `AssistantMessage.usage`, attached to the first step of a turn (for budget accounting) |

> **Grounding note for the summarizer.** The observation-grounded outcome rule
> (tex §4) is satisfied by reading `observation`. `meta.is_error` may be used as a
> *corroborating* signal but the result must be derivable from `observation`
> alone, because `is_error` is Pi's classification, not the raw output.

### 3.3 Example `trace.jsonl` (6 steps)

```jsonl
{"step":0,"reasoning":"First I'll see what the test suite reports so I know what's broken.","tool":"bash","args":{"command":"pytest -q"},"observation":"...\nE   ImportError: cannot import name 'verify_token' from 'auth'\n1 error in 0.42s","meta":{"tool_call_id":"call_a1","is_error":true,"stop_reason":"toolUse","turn":0,"ts":"2026-06-18T14:00:01Z","usage":{"input":1820,"output":143,"totalTokens":1963}}}
{"step":1,"reasoning":"The import fails because auth.py has no verify_token. Let me read auth.py.","tool":"read","args":{"path":"auth.py"},"observation":"def login(...):\n    ...\n# (no verify_token defined)","meta":{"tool_call_id":"call_a2","is_error":false,"stop_reason":"toolUse","turn":1,"ts":"2026-06-18T14:00:05Z"}}
{"step":2,"reasoning":"I'll add verify_token next to login.","tool":"edit","args":{"path":"auth.py","old":"def login(","new":"def verify_token(token):\n    return _decode(token) is not None\n\n\ndef login("},"observation":"edited auth.py (1 hunk applied)","meta":{"tool_call_id":"call_a3","is_error":false,"stop_reason":"toolUse","turn":2,"ts":"2026-06-18T14:00:09Z"}}
{"step":3,"reasoning":"Now re-run the tests to confirm the import resolves and the suite passes.","tool":"bash","args":{"command":"pytest -q"},"observation":"....\n4 passed in 0.51s","meta":{"tool_call_id":"call_a4","is_error":false,"stop_reason":"toolUse","turn":3,"ts":"2026-06-18T14:00:14Z"}}
{"step":4,"reasoning":"Tests pass. Run the linter as the skill instructs before finishing.","tool":"bash","args":{"command":"ruff check ."},"observation":"All checks passed!","meta":{"tool_call_id":"call_a5","is_error":false,"stop_reason":"toolUse","turn":4,"ts":"2026-06-18T14:00:18Z"}}
{"step":5,"reasoning":"All green and lint clean. I'm done; summarizing the fix.","tool":null,"args":{},"observation":null,"meta":{"stop_reason":"stop","turn":5,"ts":"2026-06-18T14:00:20Z"}}
```

---

## 4. The run manifest (`run.json`)

Carries everything about the run that is *not* per-step. Frozen contract.

| Field            | Type     | Meaning |
|------------------|----------|---------|
| `run_id`         | `string` | Unique id (ULID/UUID). Matches the directory name. |
| `schema_version` | `string` | Frozen-format version, e.g. `"trace/1"`. Bump on any breaking change to §3/§4. |
| `skill`          | `object` | `{ "name": str, "path": str, "sha256": str }` — the skill under test and a hash of its `SKILL.md` (+ scripts) so a report is pinned to an exact skill revision. |
| `input`          | `object` | The `(x, E_0)` the run was launched on: `{ "prompt": str, "containerfile": str, "base_image": str, "candidate_id": str|null }`. `containerfile` is the **full Containerfile text** that defines E₀ ([environments.md](./environments.md)); `base_image` is the pinned per-skill base its `FROM` references. `candidate_id` links back to the synthesizer's candidate (`null` for seeds). |
| `model`          | `object` | The **agent-under-test** model: `{ "provider": str, "id": str, "thinking_level": str, "temperature": number|null }`. `temperature` is `null` if not settable (see [pi-integration OQ-1](./pi-integration.md#oq-1-temperature)). |
| `runner`         | `object` | `{ "pi_version": str, "built_image": str, "network": str, "wall_clock_cap_s": int }`. `built_image` = per-test image (base cache + cheap tail); `network` = container network mode (`"host"`); `wall_clock_cap_s` is the shared timeout (no step/token/currency cap — [D-RUN-1](./environments.md#termination--budget)). |
| `termination`    | `object` | `{ "reason": "completed"|"timeout"|"error", "steps": int, "detail": str|null }`. |
| `container`      | `object` | The **live container left for the Property Checker** — `{ "name": str, "alive": bool, "cleanup_grace_s": int }`. The checker stages trace/diff evidence, snapshots its final filesystem once, runs each check in a fresh isolated child, then destroys it; the Runner's timebomb reclaims it after `cleanup_grace_s` if checking never starts (§6). |
| `seed`           | `int`    | Selection/mutation seed in effect when this input was produced (campaign reproducibility). |
| `created_at`     | `string` | ISO8601. |

### 4.1 Example `run.json`

```json
{
  "run_id": "01JZ8Q2example",
  "schema_version": "trace/1",
  "skill": { "name": "fix-failing-test", "path": "skills/fix-failing-test", "sha256": "9f2c…" },
  "input": {
    "prompt": "The test suite is failing. Make it pass.",
    "containerfile": "FROM skillrace/fix-failing-test:base@sha256:9f2c…\n# >>> SKILLRACE TAIL >>>\n…seed_03 scenario…\n# <<< SKILLRACE TAIL <<<\n",
    "base_image": "skillrace/fix-failing-test:base@sha256:9f2c…",
    "candidate_id": null
  },
  "model": { "provider": "yunwu", "id": "glm-4.5-flash", "thinking_level": "medium", "temperature": null },
  "runner": { "pi_version": "0.73.1", "built_image": "skillrace/run-01JZ8Q2example:built", "network": "host", "wall_clock_cap_s": 900 },
  "termination": { "reason": "completed", "steps": 6, "detail": null },
  "container": { "name": "run-01JZ8Q2example", "alive": true, "cleanup_grace_s": 300 },
  "seed": 1337,
  "created_at": "2026-06-18T14:00:21Z"
}
```

---

## 5. Normalization rules: Pi session → frozen trace

The Runner converts Pi's event/session stream into `trace.jsonl`. The mapping is
the entire risk surface of the Runner and is tested in isolation against recorded
Pi sessions (see [runner.md](./design/runner.md)). All rules below are
**decisions** that resolve underspecification in the tex; each is testable.

| Pi construct ([session-format](https://pi.dev/docs/latest/session-format), [json](https://pi.dev/docs/latest/json)) | Frozen-trace treatment |
|---|---|
| `AssistantMessage.content[]` block `{type:"thinking", thinking}` | Contributes to `reasoning` (see D-TRACE-1). |
| `AssistantMessage.content[]` block `{type:"text", text}` | Contributes to `reasoning` (see D-TRACE-1). |
| `AssistantMessage.content[]` block `{type:"toolCall", id, name, arguments}` | Becomes a step's `tool`/`args`; `id` → `meta.tool_call_id`. |
| `ToolResultMessage{toolCallId, content[], isError}` | Matched to its step by `toolCallId`; `content[]` text flattened → `observation`; `isError` → `meta.is_error`. |
| `BashExecutionMessage{command, output, exitCode, …}` | If present as its own entry (RPC `bash` path), mapped as a `bash` step with `args.command`, `observation=output`, and `meta.exit_code`. (Normally bash arrives as a normal `bash` toolCall + toolResult.) |
| `UserMessage` (the initial prompt) | **Not** a step. Recorded in `run.json.input.prompt` only. Subsequent user messages (steering/follow-up) are not expected in headless runs; if present they are recorded as `meta.injected_user` markers on the next step and counted. |
| `type:"compaction"` / `CompactionSummaryMessage` | **Not** a step. The normalizer must linearize across it (see D-TRACE-4). A `meta.compaction_before=true` flag is set on the first step after a compaction so segmentation/property checks can be aware. |
| `type:"branch_summary"` / branch entries | A headless single-prompt run is not expected to branch. If the session tree branches, the normalizer takes the **path from the active leaf to the root** (Pi's "current position" semantics) and ignores abandoned branches. Recorded in `run.json.termination.detail`. |
| `model_change`, `thinking_level_change`, `label`, `custom`, `session_info` | Ignored for the trace; not steps. |

### Decisions

- **D-TRACE-1 (reasoning = thinking + text).** `reasoning` is built from the
  assistant turn's pre-tool-call content: if a `thinking` block is present, use it;
  if a `text` block is also present, append it after a blank line
  (`thinking + "\n\n" + text`); if only `text`, use `text`; if neither, `""`.
  *Why it matters for testability:* segmentation and guard extraction read
  `reasoning` as the branch signal. The Runner therefore configures the agent with
  thinking **visible** (`thinkingLevel: "medium"` or higher) so this field is rich;
  if a provider does not surface thinking, the field degrades to `text` only and
  segmentation quality drops — flagged as [OQ-4](./pi-integration.md#oq-4-thinking-capture).

- **D-TRACE-2 (one tool call per step; multi-call turns).** A single Pi assistant
  turn may emit several `toolCall` blocks. Each becomes its **own** step, in the
  order Pi lists them. The turn's `reasoning` is attached to the **first** of those
  steps; sibling steps in the same turn get `reasoning: ""` and share `meta.turn`.
  *Why:* keeps "one tool call = one step" (tex's schema) while preserving that the
  rationale was stated once for the group. Segmentation treats an empty `reasoning`
  as "no new announced goal," which is the correct semantics.

- **D-TRACE-3 (terminal step).** A final assistant turn that ends the run with no
  tool call (`stopReason:"stop"`) is emitted as one step with `tool:null`,
  `args:{}`, `observation:null`, and `reasoning` = its text/thinking. This gives
  property checks a concrete "final episode" to anchor on (e.g. "lint precedes the
  final episode"). If the run ends by hitting the step cap mid-turn, no terminal
  step is appended and `termination.reason="timeout"` (or `"token_budget"`).

- **D-TRACE-4 (compaction linearization).** Pi may compact context mid-run
  ([compaction](https://pi.dev/docs/latest/compaction)). Compaction entries are
  bookkeeping, not agent actions; they are dropped from the trace, step numbering
  continues unbroken across them, and `meta.compaction_before=true` marks the next
  real step. *Testability impact:* a long run that compacts must still yield a
  contiguous `step` sequence; this is asserted by a fixture test.

- **D-TRACE-5 (observation flattening & truncation).** `observation` is the
  concatenation of the tool result's text content blocks. Image blocks are replaced
  by `"[image omitted: <mimeType>]"`. Very large outputs are truncated to
  `OBS_MAX_BYTES` (default 64 KiB) with a trailing
  `"\n…[truncated N bytes]"` marker; the untruncated text is preserved in
  `raw/`. *Testability impact:* truncation is deterministic given `OBS_MAX_BYTES`,
  recorded in `run.json.runner`, so summaries are reproducible.

---

## 6. Final container state (snapshotted, isolated checks, then destroyed)

Per the tex, SkillRACE does **not** snapshot the environment at each step. Instead the
**Runner leaves the final container running** and the **separate Property Checker**
([environments.md, per-run flow](./environments.md#per-run-flow-validate-then-run-in-the-same-container)):

- The checker stages trace/diff evidence and makes one filesystem snapshot of the exact
  final container. Every state/trace script then runs in a fresh networkless,
  capability-dropped child with a host timeout. No agent or model is involved, and one
  script cannot mutate evidence for another. Ephemeral non-filesystem state is outside
  this oracle boundary and is reported as a limitation.
- **A `workspace_snapshot/` is copied out** (`docker cp`: git diff + changed/created
  files) for debugging and bug-report evidence.
- The **Property Checker owns teardown**: after its checks it `docker rm -f`s the
  container (+ env image). A **detached timebomb** armed by the Runner reclaims the
  container if the checker never runs.
- **Post-campaign confirmation** rebuilds the representative `input.containerfile`
  and reruns it once outside the 30-execution search budget.

`run.json.container` is the **live container name** (plus `container_alive` and
`cleanup_grace_s`). The temporary check snapshot is deleted after verdicts; the durable
reproduction inputs are the Containerfile text (in `run.json.input`), trace/diff, and
copied-out workspace evidence.

---

## 7. Well-formedness (validated by the Runner, re-checkable by anyone)

A trace is **well-formed** iff:

1. `run.json` validates against the manifest schema and `schema_version` is known.
2. `trace.jsonl` is valid JSONL; every line validates against the step schema.
3. `step` values are `0..N-1`, contiguous, strictly increasing.
4. Exactly the last step may have `tool:null`; no interior step has `tool:null`.
5. Every step with `tool != null` has non-null `args` (object) and a string
   `observation` (possibly empty).
6. `termination.steps == N`.

A standalone validator (`skillrace.trace.validate`) checks these and is reused as
the first assertion in every downstream component's tests. Malformed traces are
**rejected loudly** (non-zero exit, structured error) — never silently repaired.

---

## 8. Extending the format

The five core fields are frozen. To add a field:

1. It goes in `meta` first (non-contract) and bakes there for at least one
   milestone.
2. Promotion to a core field requires a `schema_version` bump and updating §3, the
   JSON Schema, and every component's fixtures.

This rule exists so that "the trace format is the contract" stays true in practice,
not just on paper.

---

## 9. JSON Schema (machine-checkable)

`schemas/trace.step.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "SkillRACE frozen trace step",
  "type": "object",
  "required": ["step", "reasoning", "tool", "args", "observation"],
  "properties": {
    "step": { "type": "integer", "minimum": 0 },
    "reasoning": { "type": "string" },
    "tool": { "type": ["string", "null"] },
    "args": { "type": "object" },
    "observation": { "type": ["string", "null"] },
    "meta": { "type": "object" }
  },
  "allOf": [
    { "if": { "properties": { "tool": { "type": "null" } } },
      "then": { "properties": { "observation": { "type": "null" } } } }
  ],
  "additionalProperties": false
}
```

The manifest schema lives at `schemas/run.manifest.schema.json` (mirrors §4).
