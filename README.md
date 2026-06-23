<p align="center">
  <img src="logo.png" alt="SkillRACE" width="460">
</p>

<h1 align="center">SkillRACE</h1>

<p align="center"><b>Reasoning-Augmented Concolic Execution for Coding-Agent Skills.</b></p>

<!-- SkillRACE — banner: logo.png lives at the repo root -->

---

SkillRACE tests coding-agent *skills* (a `SKILL.md` plus optional scripts —
reusable procedural guidance an agent consults to do a task) the way concolic
execution tests code. It runs the skill, lifts each run into a sequence of
**episodes** (coherent units of work, each with a purpose and an
**observation-grounded outcome**), merges episodes into a growing **behavior
tree**, reads the agent's reasoning at each branch as the **condition** that sent
it one way rather than another, then **negates or mutates** those conditions to
synthesize new `(prompt, environment)` inputs that drive the skill down branches it
has not yet taken. Each new run folds back into the tree, which reveals the next
unexplored branch. The payoff: (i) a **map** of how the skill's guidance maps onto
execution (what is unexplored), and (ii) a stream of **targeted tests** that expose
where the skill violates its intended behavior. Correctness is judged by
**properties** — fixed formulas and per-task compiled specifications — **not** by
asking a model "did this go well?"

The agent under test runs on the [Pi agent framework](https://pi.dev/docs/latest);
how SkillRACE uses Pi is documented exhaustively (with citations and open questions)
in **[docs/pi-integration.md](./docs/pi-integration.md)**.

> This repository currently contains the **developer documentation and build plan**.
> The design source of truth is `skillrace-implementation.tex`; this doc set
> translates it into exact contracts, per-component specs, and an implementation
> order for a developer who has not read the tex.

---

## The composability principle (non-negotiable)

SkillRACE is **six components plus baselines**, and **every component is
independently runnable and independently testable**:

- each has a **precisely defined input and output format**;
- each **reads its input from a file (or a well-defined data structure) and writes
  its output likewise**, so it can be exercised in isolation with fixed inputs and
  its output inspected — **without running the rest of the pipeline**;
- **no component reaches into another's internals**; they communicate **only**
  through their declared I/O contracts ([docs/data-contracts.md](./docs/data-contracts.md)).

This exists so each piece can be confirmed working on its own *before* the loop is
assembled. Concretely, every component is a pure function with two entry points
([data-contracts §0](./docs/data-contracts.md#0-the-universal-component-contract)):

```bash
# library:  run(input, config) -> output        (used by unit tests with fixtures)
# CLI:      python -m skillrace.<component> --in <path> --out <path> [--config <path>]
```

The orchestrator (the loop) is the *only* thing that wires components together, and
it does so by **passing files** — never by calling into a component's internals.

---

## The six components + baselines

| # | Component | Cost | Model role | Doc |
|---|-----------|------|------------|-----|
| 1 | **Runner** — runs the skill's agent in Docker via Pi, logs the frozen trace | **expensive** (the agent under test) | none of SkillRACE's own | [runner.md](./docs/design/runner.md) |
| 2 | **Episode Segmenter** — splits a trace into purpose-labeled episodes (causal, windowed) | cheap | 1 call/window | [episode-segmenter.md](./docs/design/episode-segmenter.md) |
| 3 | **Episode Summarizer** — structured summary whose **result is read from tool outputs, never narration** | cheap | 1 call/episode | [episode-summarizer.md](./docs/design/episode-summarizer.md) |
| 4 | **Tree Builder** — folds episodes into the behavior tree by **similarity merge** (riskiest model step) | cheap | merge + broaden | [tree-builder.md](./docs/design/tree-builder.md) |
| 5 | **Guard Extractor & Test Synthesizer** — distills branch conditions, mutates them, **validates before any agent run** | cheap + code validator | extract + generate | [guard-synthesizer.md](./docs/design/guard-synthesizer.md) |
| 6 | **Property Checker** — judges correctness; SBE specs **compiled per task** into mechanical checks | code (+ model at compile time) | compile-time only | [property-checker.md](./docs/design/property-checker.md) |
| — | **Baselines** — random / VeriGrey-inspired greybox / SkillRACE, as **drop-in generators** | — | per rung | [baselines.md](./docs/design/baselines.md) |

**Cost asymmetry drives the whole design:** five components are cheap (code or a
small fast model); **one** — running the agent under test — is expensive. So: spend
agent runs only on inputs already *validated*, and do everything else with cheap
models or code.

**One model, ablated — not mixed.** A **single** model is used for *every*
model-driven step (segmentation, summarization, merge, guard extraction, SBE
compilation). Model choice is an **ablation axis** (swap it, report how the numbers
move), **not** a per-component decision. The agent under test is whatever agent the
skill targets and is independent of this choice.

---

## How the components connect

Arrows are **artifacts** (files/JSON), not function calls. Full schemas:
[docs/data-contracts.md](./docs/data-contracts.md).

```
 Candidate (x, E0)                                           PropertySpec[]  (per skill)
      │                                                            │
      ▼                                                            ▼
 ┌──────────┐  trace.jsonl + run.json   ┌────────────┐  Episode[]  ┌─────────────────────┐
 │ 1 Runner │ ────────────────────────▶ │ 2 Segmenter│ ──────────▶ │ 6 Property Checker  │── BugReport[]
 │  (Pi)    │                           │ 3 Summarizer│            └─────────────────────┘
 └──────────┘                           └────────────┘                   ▲ reads trace + final container
      ▲                                       │ Episode[]
      │ validated Candidate                   ▼
 ┌─────────────────────────┐  Guard/   ┌────────────┐  Frontier
 │ 5 Guards + Synth + 5c    │◀──────────│ 4 Tree     │────────────┐
 │    Validator (no agent)  │  branch   │  Builder   │            │ (selection policy)
 └─────────────────────────┘           └────────────┘◀───────────┘
```

**The loop** (tex §"The full loop"): seed the tree with `--seed-count` runs → pick a
branch by priority (fan-out / mid-depth / novelty) → mutate its guard (negate / ask
for a novel diverse sibling) → synthesize and **validate** a candidate (no agent run)
→ run the agent once, **checking state-based properties live in the same container**
→ fold the trace back in and classify it (predicted divergence / spurious merge →
**split** / path miss) → trace-structural checks (+ optional k-fold regrade) → repeat
until the run budget (~100–150 runs/skill) is spent.

---

## Key design points (and where they're nailed down)

- **The frozen trace format is the contract** between the Runner and everything
  downstream — defined exactly, as a projection of what Pi produces:
  [docs/trace-format.md](./docs/trace-format.md).
- **An environment E₀ is one Containerfile** (per-skill cached base + a cheap
  per-test tail), validated and then run **in the same container** (isolation by
  destruction; host networking for model egress): [docs/environments.md](./docs/environments.md).
- **Segmentation is causal and windowed** — a committed boundary is never revised by
  later context; the uncommitted tail carries to the next window:
  [episode-segmenter.md](./docs/design/episode-segmenter.md).
- **The episode summary's `result` is the outcome, read from tool outputs, never
  from narration** — the correctness-critical rule:
  [episode-summarizer.md](./docs/design/episode-summarizer.md).
- **Tree nodes are episodes merged on `attempt`+`target` (a model judgment), and the
  differing *outcome* becomes the guard on the diverging next edge** — so the outcome
  is the branch condition, not part of node identity. Summaries **broaden** on merge;
  consistency rests on **temperature-0 caching + spurious-merge/split** — the riskiest
  model step, tested against labeled pairs: [tree-builder.md](./docs/design/tree-builder.md).
- **Guards use two distinct signals** — the prior episode's *outcome* (from tool
  outputs) and the next episode's *opening reasoning* (from reasoning text):
  [guard-synthesizer.md](./docs/design/guard-synthesizer.md).
- **Test synthesis validates candidates against the built container before any agent
  run** (only setup-decidable guards in v1); the validator is a separate testable
  unit: [guard-synthesizer.md §5c](./docs/design/guard-synthesizer.md#5c-validator-the-key-efficiency-move--a-separate-unit).
- **Property checking has two orthogonal axes** (trace-structural vs state-based;
  fixed vs SBE); **SBE specs are compiled per task by a model into an executable
  check** that runs mechanically — the model runs only at compile time and the
  generated checks are inspectable artifacts:
  [property-checker.md](./docs/design/property-checker.md).
- **The three-rung baseline ladder shares the runner, environments, and property
  checker** — the baselines are drop-in alternatives to SkillRACE's *generation*
  component, not separate systems: [baselines.md](./docs/design/baselines.md).

---

## Running it

### The whole loop on one skill

```bash
skillrace campaign \
  --skill skills/fix-failing-test \
  --method skillrace \                 # or: random | greybox
  --seed-count 20 \                    # how many tests to sample to seed the tree
  --budget 120 \                       # agent runs
  --model anthropic/claude-opus-4-8 \  # the single judgment model (ablation axis)
  --out out/
```

Outputs land under `out/<method>/<skill>/`: numbered run directories `000/ 001/ …`
plus the skill-level `tree.json`, `frontier.json`, `coverage.json`, and `bugs/`
(see [trace-format.md §2](./docs/trace-format.md#2-on-disk-layout)). Per tex
§"Outputs": the **behavior tree + coverage report**, the **bug reports** (each with
violated property, mutated assumption, replayable Containerfile repro), and
**per-component agreement numbers** for the model-driven steps.

### Each part alone (the composability payoff)

Every component runs standalone on fixed inputs — this is how you confirm a piece
works before assembling the loop:

```bash
R=out/skillrace/fix-failing-test/000      # a run directory

# 0. (once) build the shared pi-base, then the per-skill base image
skillrace build-base   --skill skills/fix-failing-test

# 1. Runner: one agent run on a candidate → a frozen trace (+ live state checks, snapshot)
python -m skillrace.runner       --in candidates/cand-01.json     --out "$R/"

# 2. Segmenter: a trace → episode boundaries
python -m skillrace.segmenter    --in "$R/trace.jsonl"            --out "$R/segmentation.json"

# 3. Summarizer: trace + segmentation → episode summaries (+ Episode[] join)
python -m skillrace.summarizer   --in "$R/"                       --out "$R/episodes.json"

# 4. Tree Builder: fold one run's episodes into the tree
python -m skillrace.tree         --in "$R/episodes.json" --tree out/skillrace/fix-failing-test/tree.json --out out/skillrace/fix-failing-test/tree.json

# 5a/5b. Guards + synthesis: a tree branch → a candidate (Containerfile tail)
python -m skillrace.guards       --in out/skillrace/fix-failing-test/tree.json --branch b1 --out candidates/cand-02.json
# 5c. Validate a candidate WITHOUT running the agent
python -m skillrace.validate     --in candidates/cand-02.json     --out "$R/validation.json"

# 6. Property checker: a finished run → verdicts + bug reports
python -m skillrace.properties   --in "$R/" --specs skills/fix-failing-test/properties/ --out "$R/verdicts.json"

# validate any trace against the frozen schema (reused as the first assert everywhere)
python -m skillrace.trace.validate --in "$R/"
```

Because each consumes/produces files matching [docs/data-contracts.md](./docs/data-contracts.md),
you can hand-craft an input, run one component, and inspect its output in isolation.

---

## Repository layout

```
skillrace-implementation.tex        # design source of truth
README.md                           # this file
docs/
  trace-format.md                   # the FROZEN trace + run manifest (the core contract)
  data-contracts.md                 # every inter-component schema, in one place
  environments.md                   # E₀ = one Containerfile; layering, validate-then-run, isolation, network
  pi-integration.md                 # exactly how Pi is used, with citations + open questions
  build-plan.md                     # implementation order + the test that gates each step
  design/
    runner.md  episode-segmenter.md  episode-summarizer.md
    tree-builder.md  guard-synthesizer.md  property-checker.md  baselines.md

# (planned, per the build plan)
skillrace/                          # Python package: one module per component + loop
schemas/                            # JSON Schemas for every contract
images/Dockerfile.pi-base           # shared pi-base (official Dockerfile.pi + pi-agent-budget)
skills/<name>/                      # SKILL.md + scripts, Containerfile.base (FROM pi-base),
                                    #   seeds/*.json (each a Containerfile), properties/
tests/fixtures/                     # golden traces, labeled merge pairs, recorded model responses
out/<method>/<skill>/<NNN>/         # per-run artifacts; tree.json/frontier.json/bugs/ at skill level
candidates/                         # synthesized (x*, E0*) candidates
```

---

## Status & open questions

The design is fully specified here; implementation follows
[docs/build-plan.md](./docs/build-plan.md) (Runner + trace first, then per-trace
processors, tree, guards/synthesis, property checker, loop, baselines — each gated
by an isolation test).

**Open questions about Pi** that the design depends on are recorded explicitly in
[docs/pi-integration.md §6](./docs/pi-integration.md#6-open-questions), each with a
documented fallback; **OQ-1/2/6 are resolved by design**, the rest are confirmed in
**Milestone 0** against a real Pi install:

- **OQ-1 (temperature) — resolved by design.** Pi does **not** expose temperature; we
  accept the agent runs at Pi's default and set `model.temperature=null`. **Components
  2–6 bypass Pi and set temperature 0 directly**, which is where the determinism/
  caching guarantees live; run determinism is the tex's empirically-reported number.
- **OQ-2 (step cap) — resolved by design.** Dropped in favour of a **wall-clock
  timeout** + optional `pi-agent-budget` token cap (D-RUN-1); no custom extension.
- **OQ-6 (cost) — resolved by design.** `pi-agent-budget` extension does cost
  tracking + the optional hard cap.
- **OQ-4 (thinking capture)** is the only OQ still affecting *correctness* (reasoning
  is the segmentation/guard signal) — run with `thinkingLevel:"medium"`, degrade to
  `text` if a provider redacts thinking. **OQ-3/5** (session linearization, `--skill`
  scoping) have fallbacks and are confirmed in Milestone 0.

Design choices made where the tex underspecifies an implementation detail are
recorded as **Decisions** (`D-TRACE-*`, `D-PI-*`, `D-ENV-*`, `D-RUN-*`, `D-TREE-*`)
in the relevant doc, with any that
materially affect testability called out.
