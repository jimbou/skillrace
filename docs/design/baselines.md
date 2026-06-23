# Baselines — the three-rung ladder

> **Design spec** (baseline ladder, from the tex). The *implemented* test-case generator (`gen_agent`) is the floor/seed generator — see [docs/generator.md](../generator.md).

> tex §9 ("Baselines: the three-rung ladder"). The baselines are **not separate
> systems**. Each is a drop-in `Generator` (the same interface SkillRACE's
> generation component implements), wired into the **same loop**, sharing the
> **same Runner, Docker environments, run budget, episode abstraction (where
> needed), and — critically — the same Property Checker**. So any measured
> difference reflects **test generation**, not detection.

---

## Purpose

To isolate, rung by rung, *what each ingredient buys*:

| Rung | Adds exactly one ingredient | Isolates |
|------|------------------------------|----------|
| **Floor — random mutation** | nothing (blind mutation) | the black-box fuzzing floor |
| **Greybox — VeriGrey-inspired** | tool-sequence **novelty feedback** | value of *any* behavioral feedback over blind mutation |
| **SkillRACE** | episodes + reasoning-derived guard mutation + behavior tree | value of *the episode abstraction & reasoning-guard mutation* over raw tool-sequence novelty |

`Floor → Greybox` isolates the value of *any* behavioral feedback. `Greybox →
SkillRACE` is the **headline claim**, measured against a real published mechanism
(VeriGrey's feedback idea) rather than a strawman.

---

## The shared interface (what makes them drop-in)

All three implement the [`Generator`](../data-contracts.md#11-the-shared-generator-interface-baselines-are-drop-in)
protocol — and **nothing else changes** in the loop:

```python
class Generator(Protocol):
    def seed(self, seeds: list[Candidate]) -> None: ...
    def propose(self) -> Candidate | None: ...        # next input, or None when exhausted
    def fold(self, candidate: Candidate, run_dir: Path) -> None: ...  # ingest a finished run
    def state(self) -> dict: ...
```

The loop (`skillrace.loop`) is written once against this protocol:

```python
gen = make_generator(method)          # "random" | "greybox" | "skillrace"
gen.seed(load_seeds(skill))
while budget_remaining():
    cand = gen.propose()
    if cand is None: break
    run_dir = runner.run(cand)                          # SHARED Runner
    verdicts, bugs = property_checker.check(run_dir, applicable_props)  # SHARED checker
    gen.fold(cand, run_dir)
    record(cand, run_dir, verdicts, bugs)
```

Only `make_generator(method)` differs across rungs. The Runner call and the
Property Checker call are byte-identical, which is the experimental control: the
three rungs cannot differ in *how a bug is detected*, only in *which inputs they
generate*.

---

## Rung 1 — Floor (random mutation)

- **`propose`:** a model mutates a randomly chosen seed's prompt and/or env at
  random; every mutant is run (no feedback).
- **`fold`:** no-op.
- **`state`:** just the RNG seed and the seed pool.
- **Uses:** the shared Runner + Property Checker. **No** episodes, **no** tree.
- This is the standard black-box fuzzing baseline and is structurally the black-box
  baseline used by VeriGrey.

---

## Rung 2 — Greybox (VeriGrey-inspired tool-sequence feedback)

Ports VeriGrey's *feedback idea* to this setting: the **novelty of the tool-call
sequence** (a new tool, a new transition, a new sequence vs. what's been seen)
drives which seeds to keep and how much to mutate them — over the **same
schematized tool events** SkillRACE uses.

- **`fold`:** schematize the run's tool-call sequence (from the frozen trace's
  `tool` fields) into events/transitions; update a **novelty index** (e.g. a set of
  seen tool n-grams / transition edges). A run that exhibits a new tool, transition,
  or sequence is "interesting" and its candidate is kept as a new seed.
- **`propose`:** pick/keep seeds by tool-sequence novelty and mutate them (generic
  mutation).
- **`state`:** the novelty index + seed pool.
- **Crucially this rung is the "no-reasoning, no-intent-layer" ablation:** it
  explores by **raw tool-sequence novelty** — **no episodes, no reasoning-derived
  guards, no behavior tree.** It reads only the `tool` field of the trace, never
  `reasoning`, never the observation-grounded outcome.
- **Honest caveat (tex §9):** VeriGrey's *injection-specific mutation* and *injection
  oracle* are **replaced** by generic mutation and our property checker. So this is
  "VeriGrey-inspired," not VeriGrey; the comparison speaks to "tool-sequence
  feedback vs. reasoning-guard mutation for correctness testing," not to VeriGrey's
  security performance.

---

## Rung 3 — SkillRACE

The full generation component: `fold` runs Components 2→3→4→5a (segment →
summarize → fold into tree → extract guards); `propose` runs 5-frontier→5b→5c
(pick a frontier branch → mutate guard → synthesize → validate) and returns a
**validated** candidate.

- **Uses:** everything; the only rung that reads `reasoning`, builds episodes, and
  maintains the behavior tree.
- Implemented as `SkillRACEGenerator` wrapping Components 2–5; it is the reference
  implementation of the `Generator` protocol.

---

## Dependencies

**Needs (all rungs):** the shared **Runner**, **Docker environments**, **Property
Checker**, the loop, the seed set, and the judgment model (random/greybox use it
only for mutation; SkillRACE uses it for all its model steps).

**Does NOT depend on:** each other. Swapping `make_generator(method)` is the only
change. The Runner and Property Checker have **no knowledge** of which rung is
driving them.

---

## The model's role

- **Random:** one model call per `propose` (mutate a seed).
- **Greybox:** one model call per `propose` (mutate a chosen seed); `fold`'s novelty
  index is **code, no model**.
- **SkillRACE:** model calls inside Components 2–5 as documented there.

All rungs use the **same** judgment model (global single-model rule); model choice
is the ablation axis, applied identically to all rungs.

---

## How to test it in isolation

The point of the baselines is comparability, so the tests assert *sharing* and
*drop-in equivalence*:

- **`shared_runner_and_checker/`** — run each rung for a tiny budget against the same
  stub skill; assert all three call the **identical** Runner and Property Checker
  entry points (spy on them) with the same signatures, and that swapping the
  generator changes **only** the proposed candidates.
- **`greybox_novelty_index/`** (pure, no model) — feed two runs with identical tool
  sequences; assert the second is **not** "interesting" (no new novelty). Feed a run
  with a new tool transition; assert it **is** kept.
- **`greybox_reads_no_reasoning/`** — assert the greybox generator never accesses the
  `reasoning`/episode/summary fields (e.g. provide a trace with `reasoning` blanked
  and confirm identical behavior) — proving it is the no-reasoning ablation.
- **`random_is_feedback_free/`** — assert `fold` is a no-op (state after `fold`
  equals state before).
- **`generator_protocol_conformance/`** — a single parametrized test that runs all
  three through the `Generator` protocol contract (seed → propose → fold → state)
  and asserts each emits a valid `Candidate` and a serializable `state`.

### Test shape

```python
@pytest.mark.parametrize("method", ["random", "greybox", "skillrace"])
def test_rung_is_drop_in(method, spy_runner, spy_checker):
    run_loop(make_generator(method), seeds, budget=3,
             runner=spy_runner, checker=spy_checker)
    assert spy_runner.signature == RUNNER_SIG          # identical Runner interface
    assert spy_checker.signature == CHECKER_SIG        # identical Property Checker
```

---

## Failure modes

| Situation | Behavior |
|-----------|----------|
| A rung tries to use a non-shared detector | Forbidden by construction: the loop hard-codes the shared Property Checker; a rung has no path to its own oracle. (Reviewed + asserted by `shared_runner_and_checker`.) |
| Greybox novelty index saturates (everything seen) | `propose` returns `None` (exhausted) → loop ends for that budget; reported as coverage plateau. |
| Random mutation produces an invalid env | Random has no validator, so the Runner's env-build failure is recorded as a wasted run (this *is* the floor's inefficiency, and the comparison is meant to show it). SkillRACE avoids this via 5c validation — exactly the efficiency the ladder is built to measure. |
| Optional security slice (VeriGrey injection oracle) | Kept **secondary**; run on a subset only, to show SkillRACE is not worse on VeriGrey's own task. Does not touch the correctness story or the shared checker. |

**Surfacing:** per-method results are attributed via `Candidate.provenance.source`
(`random`/`greybox`/`skillrace`), so bug yield, coverage, and wasted-run counts are
reported per rung against the identical detector.
