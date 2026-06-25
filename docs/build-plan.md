# Build Plan

The order to implement SkillRACE so that **each component is testable in isolation
the moment it lands**, with the exact test that gates moving on, ending in one
end-to-end integration test on a single skill. The order follows the data
dependencies: the **Runner and the frozen trace first** (everything consumes it),
then the **per-trace processors**, then the **tree**, then **guards/synthesis**,
then the **property checker**, then the **assembled loop**, then the **baselines**.

The guiding constraint is the composability principle: every component is a pure
function over file/JSON contracts ([data-contracts.md](./data-contracts.md) §0), so
each stage is validated against **fixed inputs** before the next is built.

---

## Cross-cutting setup (do once, before Milestone 1)

These exist so every later test is offline, deterministic, and cheap.

- **Repo skeleton & contract convention.** `skillrace/<component>.py` each exposing
  `run(input, config)->output` + a `__main__` CLI (`--in/--out/--config`). One
  shared `skillrace/io.py` for JSON/JSONL (de)serialization and the
  `split-on-\n-only` reader ([pi-integration §2.2](./pi-integration.md#22-two-capture-paths-one-durable-one-streaming)).
- **Schemas.** `schemas/*.schema.json` for every contract in
  [data-contracts.md](./data-contracts.md); a `validate_*` helper per schema. The
  trace/manifest validators ([trace-format §7](./trace-format.md#7-well-formedness-validated-by-the-runner-re-checkable-by-anyone))
  are the first assertion in every downstream test.
- **Recorded-model harness.** A `RecordedModel` that maps a prompt hash → a stored
  response (so judgment steps are deterministic offline) and a `ForbiddenModel`
  that raises if called (to prove a path is model-free). A `record` mode captures
  real responses into fixtures on demand.
- **A tiny stub skill + fixture images.** `skills/hello/` (trivial SKILL.md) and a
  `fix-failing-test` toy skill with a small `Containerfile.base` (built/cached once),
  2–3 seed Containerfiles (FROM base + tail), and a prebuilt final-state image for
  property tests. These make Runner/validator/checker tests runnable on a laptop.
- **Milestone 0 (Pi smoke test).** Resolve the open questions in
  [pi-integration §6–7](./pi-integration.md#6-open-questions) against a real Pi
  install **before** the Runner is declared done: temperature pinning (OQ-1), step
  cap (OQ-2), session linearization (OQ-3), thinking capture (OQ-4), `--skill`
  scoping (OQ-5), usage (OQ-6). Each result is written back into pi-integration.md.

---

## Milestone 1 — Runner + frozen trace (the foundation)

**Why first:** the [frozen trace](./trace-format.md) is the contract between the
Runner and *everything* downstream. Freeze it now; nothing else can be tested
against real data until it exists.

**Build, in sub-order:**
1. The trace/manifest **schemas + validator** (`skillrace.trace.validate`).
2. The **normalizer** `normalize(raw_session)->(trace, manifest)` — the pure
   projection of Pi's session ([trace-format §5](./trace-format.md#5-normalization-rules-pi-session--frozen-trace)).
   This is the risk surface; build it against recorded Pi sessions.
3. The **per-skill base image build** (`skills/<name>/Containerfile.base` → cached
   `…:base@sha256:…`) and the **per-test Containerfile build** (FROM base + cheap
   tail), with the **structure-rule enforcement** ([environments.md](./environments.md)).
4. The **orchestration / container lifecycle**: build the candidate Containerfile,
   start a **long-lived** container (`--network=host`, `sleep infinity`, key via
   `-e`), run the agent via `docker exec` (`pi --print --session … --skill …`) under
   a **wall-clock timeout** (+ optional token cap), capture session + diff into the
   run dir, **`docker cp` the `workspace_snapshot/`**, then **leave the container
   running** (record `run.json.container`) + arm a detached **timebomb**; write
   `run.json`. The Runner runs **no property checks** and does **no `docker commit`**
   — the [Property Checker](#milestone-5--property-checker-shared-by-all-rungs) execs
   into the live container afterward and tears it down.
5. A **`--dry-run`** path (build only, no agent) for quick container-build checks.

**Isolation test that gates the next milestone:**
- *Normalizer golden tests* over `tests/fixtures/runner/` covering D-TRACE-1…5,
  OQ-3 linearization, and the malformed-input loud-failure case
  ([runner.md → How to test](./design/runner.md#how-to-test-it-in-isolation)).
  **Run with no Pi process and no Docker.**
- *Validator self-test:* every produced trace passes `validate`; a hand-crafted
  malformed trace is rejected.
- *Environment tests* ([environments.md → test in isolation](./environments.md#how-to-test-this-in-isolation)):
  base layers are **cache hits** while only tail layers rebuild; structure-rule
  violations are rejected pre-build; isolation-by-destruction (a dirtying run leaves
  no trace on the next; host cwd unchanged); the run command carries `--network=host`
  and no secret is baked into the image.
- *Orchestration integration (Docker):* the agent runs in a long-lived container that
  is **left alive** after (its name in `run.json.container`); the wall-clock
  **timeout** (and optional token cap) fire; `workspace_snapshot/` is copied out; the
  **timebomb** removes the container after the grace period if no checker runs; **no
  `docker commit`**.

**Definition of done:** feeding any recorded Pi session yields a well-formed,
golden-matching frozen trace; a real (tiny) skill run produces a run directory that
`validate` accepts. **Now every downstream component has a real input to test on.**

---

## Milestone 2 — Per-trace processors (Segmenter, then Summarizer)

**Why second:** both consume only the frozen trace (Milestone 1). They are
independent of each other and of the tree, so they can be built and tested purely
from trace fixtures.

### 2a. Episode Segmenter
**Build:** windowing loop with uncommitted-tail carry-over + force-commit; strict
JSON validation + one retry; `unsegmentable` flag.
**Gating isolation test** ([episode-segmenter.md](./design/episode-segmenter.md#how-to-test-it-in-isolation)):
- the **causality test** (episodes committed in window 1 are identical whether or
  not later steps exist) — *the* defining test;
- partition/evidence-range invariants enforced even on malformed model output;
- uncommitted-tail carry-over; force-commit rate; `unsegmentable` counted.
All with a `RecordedModel` (offline).
**Done:** golden segmentation on the canonical trace; causality test green.

### 2b. Episode Summarizer + the `Episode` join
**Build:** per-episode summary with the **observation-grounding check** (`evidence ⊆
observation`); the trivial code join into `Episode[]`.
**Gating isolation test** ([episode-summarizer.md](./design/episode-summarizer.md#how-to-test-it-in-isolation)):
- the **`false_victory` test** — observation says "failed", reasoning says "passed";
  assert the result reflects the **observation** and the narration never leaks in.
  This is the correctness-critical rule made into a test.
- `no_observation` fallback; grounding-rejection → retry.
**Done:** golden `Episode[]` for the canonical run; false-victory test green.

---

## Milestone 3 — Tree Builder (online merging)

**Why third:** consumes `Episode[]` (Milestone 2). The riskiest model step, so it
gets the most isolation testing before anything depends on its output.

**Build:** the fold algorithm (**merge on attempt+target; outcome→edge**, D-TREE-1);
the `same_action` model call with **content-hash caching** (key hashes attempt+target,
not outcome); monotone `broaden`; the `Frontier` view + priority scoring (fan-out /
mid-depth / novelty, D-TREE-2). (No `split` — dropped from the design; differing
outcomes are handled by the outcome-on-edge + downstream branch, see tree-builder.md.)

**Gating isolation tests** ([tree-builder.md](./design/tree-builder.md#how-to-test-it-in-isolation)):
- **Merge decision against the labeled pair set** (`merge_pairs.jsonl`, labeled on
  attempt+target ignoring outcome): offline determinism (pure function of `pair_key`),
  symmetry, cache-hit-no-call; plus the calibration-vs-labels number.
- **Fold mechanics** with `same_action` stubbed: the **D-TREE-1 case** (same action,
  different outcome → one merged node that is a branch, with the outcome on each
  out-edge); divergent actions → branch; `broaden` monotonicity; frontier ordering.
- **Build stability**: two fold orders → tree agreement ≥ threshold.

**Done:** the merge decision is a pure, cached, symmetric function on fixed pairs;
the same-action/different-outcome case yields one node + an outcome-labeled branch.

---

## Milestone 4 — Guards & Test Synthesis (5a, 5b, then the keystone 5c)

**Why fourth:** consumes the tree's branches (Milestone 3). Build the **validator
(5c) with the most care** — it is the efficiency keystone.

**Build:** 5a extraction (two-signal guard + executable grounding +
`decidable_from`); 5b synthesis (negate/mutate → a `Candidate` whose **Containerfile
tail** sets up the mutated guard); **5c validator** (`validate(container, guard)`:
structure check → build (base cache + tail) → in-container checks, **no agent**),
runnable standalone or as the Runner's gate phase in the shared container.

**Gating isolation tests** ([guard-synthesizer.md](./design/guard-synthesizer.md#how-to-test-it-in-isolation)):
- 5a: import-vs-assertion guard; the **outcome/reasoning-disagreement bug signal**;
  tactics-not-referenced.
- 5b: negate (binary) and mutate (multivalued → sibling) produce candidates with
  correct `provenance.mutation`.
- **5c: the efficiency invariant test** — `validate(valid_candidate)` returns
  `valid:true` **and the agent entry point is never called** (spy raises if it is);
  invalid setup → `valid:false`, no agent run; runtime-only guard → deferred, no
  build.

**Done:** a validated candidate is producible for a known branch **without** any
agent run, proven by the spy.

---

## Milestone 5 — Property Checker (shared by all rungs)

**Why fifth:** independent of the tree/synthesis (it reads a finished run + specs),
but needed before the loop can emit bugs. Built now so the loop in Milestone 6 has a
real detector, and so the baselines in Milestone 7 share it unchanged.

**Build:** the trace-pattern evaluator (trace-structural, runs after the fold); the
**`check_state(container, …)`** executor (state-based, `docker exec`s into the **live
container the Runner left**, then the checker tears it down); the **SBE compile
step** (model at compile time only) with
the shell-allowlist validator; `PropertyVerdict`/`BugReport`; the optional **k-fold
regrade (default off)** rebuilding from the Containerfile; the injected-violation
detection harness; the per-skill applicability matrix.

**Gating isolation tests** ([property-checker.md](./design/property-checker.md#how-to-test-it-in-isolation)):
- **Evaluation is model-free & deterministic** (`ForbiddenModel` at eval time; same
  input → same verdict).
- Trace-structural (test-before-commit) and state-based (build-passes) goldens;
  `check_state` runs against a fixture container with no agent.
- SBE compile produces a check referencing the right concrete target; the
  allowlist validator rejects unsafe compiled checks.
- **Injected-violation detection rate** (delete-test / hardcode-output / force-push
  all flagged).
- Regrade (with `k=3`) classifies genuine-bug vs brittleness (Runner stubbed); with
  default `k=1` a single violation is `flagged`.

**Done:** fixed properties run with zero model calls; SBE checks compile, validate,
and run mechanically; injected violations are detected at the reported rate.

---

## Milestone 6 — The assembled loop (SkillRACE end-to-end)

**Why sixth:** all components exist and are independently green. Now wire them with
the **`Generator` protocol** ([data-contracts §11](./data-contracts.md#11-the-shared-generator-interface-baselines-are-drop-in))
— `SkillRACEGenerator` = Components 2→5 — and the shared loop (Runner + Property
Checker).

**Build:** `skillrace.loop` (seed `--seed-count` inputs → exploration loop: pick
frontier branch by priority → mutate guard (negate / **novel diverse sibling** given
all observed siblings) → synthesize → **validate + run + live state-checks in one
container** → fold → classify {predicted divergence / path miss} → trace-structural
checks → optional k-fold regrade); the selection policy
(fan-out / mid-depth / novelty, plus a property-relevance boost); budget accounting;
campaign outputs written to `out/<method>/<skill>/`.

**The end-to-end integration test (the milestone gate):**
> **`tests/integration/loop_one_skill.py`** — run the full loop on the
> `fix-failing-test` toy skill with a **small budget (e.g. 5 agent runs)**, using a
> **recorded/stubbed agent** (replayed Pi sessions) and recorded judgment-model
> responses, so it is deterministic and offline. Assert the loop:
> 1. seeds the tree from the seed candidates and produces a non-empty `BehaviorTree`
>    + `Frontier`;
> 2. picks a frontier branch, **synthesizes and validates** a candidate (validator
>    green, **no un-validated input reaches the Runner** — assert every Runner call
>    was preceded by a `valid:true` `ValidationReport`);
> 3. folds the new run and **classifies** it (at least one of predicted-divergence /
>    path-miss exercised, using crafted replays);
> 4. runs the **shared Property Checker** (separate command) — state-based checks
>    `docker exec`-ed into the **live container the Runner left** (then destroyed),
>    trace-structural over the trace/diff — and emits a
>    `BugReport` for a planted violation, with the **mutated assumption** (NL) and a
>    **replayable Containerfile repro** attached;
> 5. terminates at the budget and writes outputs under `out/skillrace/fix-failing-test/`.
>
> A second, **opt-in "live" smoke** (`@pytest.mark.live`) runs the same loop with
> the real Pi agent + real model for 1–2 runs to confirm the offline replays match
> reality — gated behind an env flag so CI stays free/offline.

**Definition of done:** the offline end-to-end test is green and demonstrates the
"validate-before-run" invariant and all three fold classifications on one skill.

---

## Milestone 7 — Baselines (drop-in generators)

**Why last:** they reuse the loop, Runner, and Property Checker unchanged
([baselines.md](./design/baselines.md)). Implement `RandomGenerator` and
`GreyboxGenerator` against the **same `Generator` protocol**.

**Gating isolation tests:**
- `generator_protocol_conformance` parametrized over all three rungs.
- `shared_runner_and_checker`: spies prove all rungs call the **identical** Runner +
  Property Checker; only proposed candidates differ.
- `greybox_reads_no_reasoning` (the no-reasoning ablation) and
  `greybox_novelty_index` (pure, no model); `random_is_feedback_free`.

**Definition of done:** swapping `make_generator(method)` is the only change needed
to run any rung; per-method attribution flows via `Candidate.provenance.source`.

---

## Dependency & gating summary

```
M0 Pi smoke ─┐
             ▼
M1 Runner + frozen trace ──▶ M2 Segmenter ──▶ M2 Summarizer/Episode ──▶ M3 Tree
                                                                          │
                                  M5 Property Checker ◀───(independent)───┤
                                       │                                  ▼
                                       └────────────▶ M6 Loop ◀── M4 Guards+Synth+Validator
                                                         │
                                                         ▼
                                                    M7 Baselines
```

Each arrow = "consumes the output contract of." Each box ships with its gating
isolation test **green against fixed inputs** before the next box starts — which is
the whole point of the composability principle: **every piece is confirmed working
on its own before the loop is assembled.**

---

## Skill suite for evaluation

Beyond the toy `fix-failing-test` skill used in fixtures, the **evaluation suite must
include at least one non-trivial, multi-file skill that produces executable
artifacts** — e.g. "scaffold/extend a small web app (Flask/React) whose several files
must work together" or "add a feature across a small existing project." Richer
projects exercise far more interesting branching (more distinct actions/outcomes) and
make the heavyweight properties bite — *build/lint passes in the final state*,
*the artifact satisfies the prompt*, *test integrity* — in ways a single-file fix
cannot. (Sourcing clean, Dockerizable skills is the real labor, per tex §3; budget
for it.) Each such skill ships its `Containerfile.base`, seeds, applicability matrix,
and SBE specs.

---

## Fixture & determinism strategy (applies to every milestone)

- **Golden JSON/JSONL** for every contract; tests diff structured output, not prose.
- **Recorded model** for judgment steps; **forbidden model** to prove model-free
  paths (validator, property evaluation).
- **Replayed Pi sessions** for the Runner/loop so no agent or API is needed offline;
  one opt-in live smoke per layer to catch replay drift.
- **Tiny prebuilt images** for state-based property checks and the validator, so
  Docker-touching tests run in seconds.
- **One seed of truth for determinism:** campaign seed + temperature-0 + the
  `MergeDecision`/SBE caches; the build-stability and merge-calibration numbers are
  produced by their own test targets and reported, per the tex's "determinism,
  honestly."

---

## What is explicitly deferred to post-v1 (recorded, not silently dropped)

- **`agent_runtime` guards** (predicates over the agent's mid-run outputs) — deferred
  and counted ([guard-synthesizer](./design/guard-synthesizer.md)).
- **The VeriGrey injection oracle slice** — optional, secondary
  ([baselines](./design/baselines.md)).
- **In-process Pi SDK runner** — CLI-in-container is v1 (D-PI-2); SDK is a later
  option if in-process control is needed.
- **Anything blocked by an unresolved OQ** in
  [pi-integration §6](./pi-integration.md#6-open-questions) is implemented behind its
  documented fallback until Milestone 0 resolves it.
