<a href="../../README.md"><img src="../../skillrace-icon.png" alt="SkillRACE" width="54" align="right"></a>

# Component 1 — Runner

> **Design spec** (Component 1, from the tex). The *implemented* command is `run_case` — see [docs/runner.md](../runner.md).

> tex §3 ("The runner and trace format"). **The only expensive component**
> (minutes and real money per run): it executes the agent under test. Everything
> else in the system is cheap precisely so that agent runs are spent only on
> pre-validated inputs.

---

## Purpose

Given a `Candidate` `(x, E_0)` — a prompt and a **Containerfile** that defines the
environment ([environments.md](../environments.md)) — the Runner builds the starting
container (per-skill base cache + cheap tail), runs the skill's agent under test
inside it via Pi (on host networking), bounds it with a **wall-clock timeout** (no
step cap), records the run as a **frozen trace** plus a **run manifest** plus a
copied-out **workspace snapshot**, and **leaves the container running** (with a
detached timebomb that removes it after a grace period). **The Runner does NOT check
properties** — that is the separate [Property Checker](../property-checker.md), which
runs *after* the run, stages evidence and snapshots the **live container the Runner
left**, executes each check in a fresh isolated child, and then destroys it.
It is a thin wrapper around Pi + Docker; its only non-trivial logic is normalizing
Pi's session into the [frozen trace format](../trace-format.md), which is the
contract every downstream component reads.

---

## Input contract

A single [`Candidate`](../data-contracts.md#1-candidate--an-input-to-the-runner)
(from a file or in-memory object) plus runner config.

```json
{
  "candidate_id": "cand-01JZ…",
  "skill": "fix-failing-test",
  "prompt": "The test suite is failing. Make it pass.",
  "base_image": "skillrace/fix-failing-test:base@sha256:9f2c…",
  "containerfile": "FROM skillrace/fix-failing-test:base@sha256:9f2c…\n# >>> SKILLRACE TAIL >>>\n…seed scenario…\n# <<< SKILLRACE TAIL <<<\n",
  "provenance": { "source": "seed", "parent_run_id": null, "branch_id": null, "mutation": null }
}
```

The environment E₀ is the candidate's **Containerfile** (one self-contained
artifact), not a base-image-plus-init-script — see
[environments.md](../environments.md). The Runner builds it (cached base + cheap
tail), optionally runs the validation gate, runs the agent in that container, and
destroys it. Optionally a `Guard` ([data-contracts §8](../data-contracts.md#8-guard--output-of-the-guard-extractor))
is passed alongside the candidate to enable the validation gate (below).

Runner config (`--config`, defaults shown):

```json
{
  "wall_clock_cap_s": 900,
  "token_cap_usd": 2.0,
  "network": "host",
  "model": { "provider": "closeai", "id": "qwen3.6-flash", "thinking_level": "medium" },
  "obs_max_bytes": 65536,
  "pi_flags_extra": [],
  "skills_root": "skills",
  "out_root": "out"
}
```

There is **no step/turn cap** — termination is by wall-clock `timeout` (primary) and
an optional `token_cap_usd` hard budget via the `pi-agent-budget` extension baked
into `pi-base` ([D-RUN-1](../environments.md#termination--budget)). This removes the
custom step-cap extension entirely.

**Where the input comes from:** seed candidates (`skills/<name>/seeds/*.json`,
authored by hand) or any `Generator.propose()` output (Component 5 or a baseline).
The Runner does not care which — `Candidate` is the whole interface.

---

## Output contract

A numbered **run directory** `<out>/<method>/<skill>/<NNN>/`
([trace-format.md §2](../trace-format.md#2-on-disk-layout)) containing:

- [`trace.jsonl`](../trace-format.md#3-the-frozen-step-schema-tracejsonl) — the frozen trace.
- [`run.json`](../trace-format.md#4-the-run-manifest-runjson) — the manifest, including `termination` and `container`.
- `workspace_snapshot/` — git diff + changed/created files `docker cp`-ed out before destroy.
- `raw/{events.jsonl,session.jsonl}` — untouched Pi artifacts (debug only; **not** a contract; no component may read these).

(Segmentation/episodes/verdicts land in the same run directory but are written by
Components 2/3/6, not the Runner.)

Example outputs are in [`trace-format.md`](../trace-format.md) §3.3 and §4.1. The
Runner guarantees the output is **well-formed** per
[trace-format.md §7](../trace-format.md#7-well-formedness-validated-by-the-runner-re-checkable-by-anyone)
before returning success; if it cannot produce a well-formed trace it fails loudly
(see Failure modes).

---

## Dependencies

**Needs:**
- **Docker** — to build the candidate's Containerfile (per-skill base cache + cheap
  tail, [environments.md](../environments.md#the-layering-per-skill-base--per-test-tail))
  and run the agent non-interactively with `--network=host` ([D-ENV-2](../environments.md#network-host-network)).
  The Runner owns the **container lifecycle**:
  **build → start detached (`sleep infinity`) → run agent via `docker exec` (baseline
  git commit, then the agent, under timeout) → `docker cp` the workspace snapshot →
  LEAVE the container running** (+ a detached timebomb to remove it after a grace
  period). It does **not** run property checks or `docker commit`; the **live
  container** + the trace are what the separate [Property Checker](../property-checker.md)
  consumes afterward (it `exec`s state checks in, then tears the container down).
- **The Pi CLI** — `pi --mode json --skill … "<prompt>"`
  ([pi-integration §2](../pi-integration.md#2-how-the-runner-drives-pi)).
- **A provider API key** for the agent-under-test model (e.g. `ANTHROPIC_API_KEY`),
  injected **at run time via `-e`** (never baked into the image, so shipped
  Containerfiles carry no secret) and reachable because the container uses host
  networking.
- **The skill directory** under `skills_root`.
- **`timeout`** (wall-clock bound) and the **`pi-agent-budget`** extension baked into
  `pi-base` (optional token hard cap). No custom step-cap extension.
- Plain code for normalization (the projection in
  [trace-format.md §5](../trace-format.md#5-normalization-rules-pi-session--frozen-trace)).

**Does NOT depend on:**
- Any other SkillRACE component (segmenter, tree, etc.). It produces the trace
  blind to who consumes it.
- A model call of its own — **the Runner makes no cheap-model judgment**. The only
  model involved is the *agent under test*, which is the agent, not a SkillRACE
  judgment step.
- The property checker or container snapshots per step (only the final commit).

---

## The model's role

**None of SkillRACE's own.** The Runner does not call the judgment model. The
expensive model here is the **agent under test**, configured in `run.json.model`
and selected via the global single-model rule only insofar as the *agent* is
whatever the skill targets (independent of the judgment-model ablation, tex §1).
The Runner's job is to *observe and record* that agent, not to judge it.

---

## How to test it in isolation

The Runner has two separable halves; test them apart.

### (a) The normalizer — pure, no Pi, no Docker (the important one)

The normalizer is `normalize(raw_session: list[dict], manifest_stub) -> (trace, manifest)`.
Feed it **recorded** Pi session/event fixtures and assert the frozen output. This
is where all the risk lives (D-TRACE-1…5), and it runs in milliseconds with no
external dependency.

Fixtures (`tests/fixtures/runner/`):
- `simple_session.jsonl` → expect the 6-step trace from
  [trace-format §3.3](../trace-format.md#33-example-tracejsonl-6-steps).
- `multi_toolcall_turn.jsonl` (one assistant turn, 3 toolCalls) → expect 3 steps,
  first with the reasoning, next two with `reasoning:""`, shared `meta.turn`
  (asserts **D-TRACE-2**).
- `terminal_no_tool.jsonl` → expect last step `tool:null, observation:null`
  (**D-TRACE-3**).
- `with_compaction.jsonl` (a `type:"compaction"` entry mid-run) → expect contiguous
  `step` numbering and `meta.compaction_before=true` on the next step (**D-TRACE-4**).
- `huge_observation.jsonl` → expect `observation` truncated at `obs_max_bytes` with
  the marker, untruncated copy in `raw/` (**D-TRACE-5**).
- `branched_session.jsonl` (a side branch) → expect leaf→root linearization, branch
  ignored (**OQ-3**).
- `malformed_missing_toolresult.jsonl` (a toolCall with no matching toolResult) →
  expect a loud `TraceNormalizationError`, not a silent drop.

Each fixture's expected output is a golden `trace.jsonl`; the test diffs JSON.
Then run `skillrace.trace.validate` on the output and assert well-formedness.

### (b) The orchestration — integration, with Docker + a stub agent

Replace the real agent with a **stub skill** whose `SKILL.md` makes the agent run a
deterministic, scripted sequence (or use a recorded `pi` replay). Assert:
- the per-test image is built from the candidate's **Containerfile** with the base
  layers as **cache hits** (only tail layers rebuild — [environments.md](../environments.md#the-layering-per-skill-base--per-test-tail));
- the run uses `--network=host`;
- **validation gate:** with a `Guard` whose setup the Containerfile does **not**
  satisfy, the agent entry point is **never called** and the container is destroyed
  (no agent run spent); with a satisfied guard, the agent runs **in the same
  container** the gate checked (assert identical container id across phases);
- the wall-clock **timeout** fires (a skill that sleeps past `wall_clock_cap_s`) →
  container killed, `termination.reason="timeout"`, partial trace still well-formed;
- the optional **token budget** trips (`token_cap_usd` set low) →
  `termination.reason="token_budget"`;
- **container left alive + timebomb:** the container stays running after the agent
  (recorded as `run.json.container`); `workspace_snapshot/` is `docker cp`-ed out; a
  detached timebomb removes the container after `--cleanup-grace` if the checker
  doesn't. The separate Property Checker snapshots the live container *afterward*,
  executes isolated check children, and tears everything down (the Runner runs no
  checks itself).

A `--dry-run` mode builds the Containerfile and runs the validation gate **without**
launching the agent — this is exactly the standalone Validator path (Component 5c)
and is cheap to test. See the [per-run flow](../environments.md#per-run-flow-validate-then-run-in-the-same-container).

### Unit/integration test shape

```python
def test_normalizer_multi_toolcall_turn():
    raw = load_jsonl("fixtures/runner/multi_toolcall_turn.jsonl")
    trace, manifest = normalize(raw, manifest_stub())
    assert [s["tool"] for s in trace] == ["bash", "read", "write"]
    assert trace[0]["reasoning"] != "" and trace[1]["reasoning"] == "" and trace[2]["reasoning"] == ""
    assert {s["meta"]["turn"] for s in trace} == {0}
    assert validate_trace(trace, manifest).ok
```

---

## Failure modes

| Situation | Behavior |
|-----------|----------|
| Candidate `containerfile` violates the structure rule (missing pinned `FROM`, second `FROM`, secret, writes outside the tail markers) | **Rejected before any build** ([environments.md](../environments.md#enforcing-the-structure-rule)); `rejected_reason` set; no build, no agent run. |
| `containerfile` fails to build the container | Abort before any agent run; emit `run.json.termination.reason="error"` with the build log in `detail`; **no trace produced**, non-zero exit. (Rare for candidates that passed validation; for raw seeds it surfaces authoring bugs.) |
| Agent exceeds the wall-clock **timeout** | `SIGKILL` the child + `docker kill` the container, `termination.reason="timeout"`, no terminal step appended. A timed-out run is **valid output** — downstream handles partial traces (if zero steps were captured, fail loudly). |
| Agent trips the **token budget** (optional) | The budget extension blocks further input; `termination.reason="token_budget"`; partial trace normalized. |
| Agent loops rapidly within the timeout (no cap stops it) | **By design**: caught later as a *property* violation ("no pathological repetition"), i.e. reported as a bug rather than hidden by a cap ([D-RUN-1](../environments.md#termination--budget)). |
| Pi process crashes / non-zero exit | `termination.reason="error"`, Pi stderr in `detail`; partial trace normalized if any events were captured, else loud failure. |
| toolCall with no matching toolResult, or non-contiguous steps after normalization | `TraceNormalizationError` (loud) — the run is **not** silently emitted as malformed. The raw artifacts are kept for diagnosis. |
| Provider/auth error inside the agent | Appears as `meta.stop_reason="error"`/`is_error` on a step; the trace is still emitted (it is a legitimate observation of the agent failing). |
| Observation exceeds `obs_max_bytes` | Truncated deterministically (D-TRACE-5); **not** an error. |

**Surfacing principle:** the Runner either produces a *well-formed* trace (possibly
recording an agent failure or a cap) or it fails *loudly* with a non-zero exit and a
structured error. It never emits a malformed trace and never silently swallows a
normalization problem. Retries are the caller's job (the loop retries Docker/network
flakes); the Runner does not silently retry the agent, because a second agent run
costs real money and would corrupt determinism accounting.
