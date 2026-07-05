<a href="../README.md"><img src="../skillrace-icon.png" alt="SkillRACE" width="54" align="right"></a>

# Implementation status — what is built, what is proven, what is left

> Written July 2026. This is the single document to read to know where the project
> stands. Component design rationale lives in `docs/design/*`; the paper is in
> `paper/skillrace.tex` (engineering design notes in `../skillrace-implementation.tex`);
> hands-on commands in [pipeline-walkthrough.md](./pipeline-walkthrough.md).

---

## 0. Status at a glance

**Built and live-verified (offline / on qwen3.6-flash):**
- The full six-component pipeline + the campaign loop, with all three method rungs
  (random floor / greybox / SkillRACE) sharing one runner and one oracle.
- Oracle: a zero-model fixed-invariant core + per-case checks compiled *before* the run.
- Injected-violation detection harness (5/5 on a pilot case).
- **D1 bug-finding suite:** 28 code-behavior skills authored (24 base-images built
  offline, 4 dependency-gated), with a pre-registered selection protocol + logged crawl.
- **D2 skill-generation suite:** 10 scenarios × 10 hidden tests = **100 tests / 192
  executable checks**, all build-verified and satisfiability-checked in-container.
- The paper (`paper/skillrace.tex`), an offline unit-test suite, and the analysis
  tooling (aggregate / calibrate / crawl / triage / driver).

**Not yet done (needs compute or human labels, not design):** the actual measurement
campaigns (RQ1/RQ3 numbers), calibration labels (RQ2), building the 4 dependency-gated
D1 skills, and loop parallelism for scale. See §5.

## 1. The system in one diagram

```text
                        ┌────────────────────────── the campaign loop (skillrace.loop) ─┐
                        │                                                               │
 SEED phase (shared):   │  EXPLORE phase (per method):                                  │
 propose K NL ideas ──▶ │   random   : fresh diverse idea, no feedback                  │
 realize (prompt,tail)  │   greybox  : VeriGrey novelty/energy over tool sequences      │
 build (+model repair)  │   skillrace: tree ─▶ guards ─▶ property-guided pick ─▶        │
                        │              synthesize ─▶ VALIDATE (no agent) ─▶ case        │
                        │                                                               │
                        │  every iteration, byte-identical for all methods:             │
                        │   compile checks (pre-run) ─▶ run agent ─▶ check properties   │
                        │   ─▶ fold (method's own feedback)                             │
                        └───────────────────────────────────────────────────────────────┘
```

## 2. Component status

| # | Component | Module | Status | Live-tested |
|---|-----------|--------|--------|-------------|
| 1 | Runner (Docker, live container + timebomb, cost, diff) | `run_case.py` | **done** | yes — 9 agent runs this session |
| 2+3 | Segmenter + summarizer (one pass; intent / what_it_did / outcome-from-tool-outputs; span validation, 1 repair; agent variant for long traces) | `segment.py`, `segment_agent.py`, `simplify_trace.py` | **done** | yes |
| 4 | Tree builder (purpose-merge, broaden-only, outcome-on-edge, way-variants, cached judgments) | `tree.py` | **done** | yes |
| 5 | Guards & synthesis (extraction w/ executable grounding + E0/agent_runtime split + disagreement flags; frontier; **property-guided selection**; synthesis; **agent-free validation**) | `guards.py` | **done** | yes — full chain fired live |
| 6a | Fixed property core (zero model, host-side Python: force-push, destructive rm, repetition, budget) | `fixed_checks.py` | **done** | yes |
| 6b | Pre-run per-case check compilation (model sees prompt + built-E0 probe, **never the run**; scripts stored with the case) | `compile_checks.py` | **done** | yes |
| 6c | Checker (executes precompiled scripts + fixed core in the live container; legacy post-hoc mode behind `--author-post-hoc`) | `check_properties.py` | **done** | yes |
| — | Seed / random generator (propose → realize → build → model-repair; toolchain-aware proposer) | `generator.py` | **done** | yes |
| — | Greybox generator (VeriGrey adaptation; L0/L1/L2 labels; corpus recycling) | `greybox.py` | **done** | yes |
| — | Campaign loop (Generator protocol; shared runner/checker; divergence classification; campaign.json) | `loop.py` | **done** | yes — all three rungs |
| — | Injected-violation detection harness | `inject_violations.py` | **done** | yes — 5/5 detected |
| — | Hidden-test skill evaluation (claim 2) | `skill_eval.py` | **done** | plumbing tested; end-to-end needs agent runs |
| — | Condition-blind skill reviser (claim 2) | `revise_skill.py` | **done** | plumbing tested; needs agent runs |
| — | Dataset crawler + triage (D1) | `crawl_skillsmp.py`, `triage_candidates.py` | **done** | yes — 628 candidates crawled + triaged |
| — | Results aggregator (campaign.json → RQ1 table + LaTeX macros) | `aggregate.py` | **done** | yes — logic unit-tested |
| — | Calibration scorers (segmentation F1 / merge kappa) | `calibrate.py` | **done** (awaits labels) | logic unit-tested |

### Key design decisions locked in this session (and why)

1. **Oracle integrity: checks compile before the run.** The model that authors a
   property check sees `(prompt, built initial environment)` and never the run it
   judges; scripts attach to the CASE so every method's runs of that case are
   judged byte-identically. This preserves the paper's "no model judges the run"
   claim and doubles as a fairness control. (`compile_checks.py`; the old
   post-hoc authoring survives only as a debugging flag.)
2. **Three verdict provenances**, reported separately: `fixed` (zero model) >
   `compiled-pre-run` (model at compile time) > `authored-post-run` (legacy).
3. **Property-guided targeting** (from notes.md): when picking which guard to
   mutate, the model is shown the skill's properties and asked for the feasible
   mutation most likely to break one — directed falsification, the main
   legitimate edge over tool-sequence novelty.
4. **Greybox = VeriGrey verbatim where possible** (novelty sets, 3-increment
   energy, novelty-weighted scheduling, corpus that never exhausts), with the
   injection-specific parts replaced by shared components, and label
   granularity (L0/L1/L2) treated as a declared, swept parameter — headline
   numbers use the baseline's best level. See
   [greybox-verigrey-adaptation.md](./design/greybox-verigrey-adaptation.md).
5. **Tree = purpose-merge, outcome on the edge, no node split.** Divergence in
   outcome surfaces one step later as a branch whose in-edges carry the
   differing outcomes — precisely where guard extraction reads them. The tex was
   updated to match.

## 3. What the live smoke tests proved (July 2026, qwen3.6-flash end to end)

| Campaign | Budget | Outcome |
|---|---|---|
| skillrace / fix-failing-test | 4 (2 seed) | 2 true syntheses, 0 fallbacks; classifications `predicted_divergence` + `no_divergence`; 2 distinct properties violated |
| greybox / fix-failing-test | 3 (2 seed) | novelty index 12 tools / 24 transitions / 3 sequences; 1 energy-chosen LLM mutation; mutant broke the wall-clock budget (counted violation) |
| random / fix-failing-test | 2 (1 seed) | clean control |
| injection study (case2) | 5 injections | **5/5 detected** (delete-test, weaken-test, false-victory, force-push, repetition) |

Notable live findings:

- **A real skill-defect case study**: a generated env named its buggy module
  `collections.py`, shadowing the stdlib so pytest itself crashed. The agent
  "fixed" the code and declared victory via a hand-rolled bypass while the real
  suite still could not run. `tests-pass-final` and `ran-tests-before-finishing`
  fired — the observation-grounded oracle caught what the agent's narration
  missed. This is the archetype of "guidance doesn't cover a broken test
  harness."
- **The Component-5 chain works as designed**: guard `which module the workspace
  targets` grounded as `test -f text_processor.py` (E0-decidable), negated,
  synthesized into a validated new env, run took a new branch
  (`predicted_divergence`); the follow-up sibling mutation produced
  `no_divergence` — the honest "stated reason wasn't causal" signal, recorded
  and the mutation marked spent.
- **A checker false positive surfaced and was fixed**: a compiled check flagged
  `rm -rf __pycache__` as destructive (the zero-model fixed core correctly did
  not). The compile prompt now instructs severity-over-surface-patterns. Keep
  collecting these — they are the compile-step calibration data the paper
  promises.

### Bugs found in pre-existing code (fixed)

- `generator.normalize_tail` prefixed `RUN` onto backslash-continuation lines,
  corrupting valid multi-line Dockerfiles (silent build-budget burner).
- Greybox seeds exhausted after spending energy → generator could die before
  budget; VeriGrey's corpus never exhausts → recycling added.
- The seed proposer suggested heavyweight foreign stacks (Spring/Rails on a
  Python base); it is now told to prefer the base's toolchain and to get
  interestingness from the broken STARTING STATE, not exotic stacks.

## 4. How to run everything

```bash
# one campaign (one method × one skill × one budget)
python -m skillrace.loop --method skillrace|random|greybox \
    --skill <s> --skill-dir skills/<s> --base skillrace/<s>:base \
    --props skills/<s>/properties.json --budget 20 --seed-count 6 \
    [--greybox-level L0|L1|L2] [--seed-k 3] --out out/campaign/<method>/<s>

# detection-rate study for a case that has compiled checks
python -m skillrace.compile_checks --case <case> --props skills/<s>/properties.json
python -m skillrace.inject_violations --case <case> --out out/injection/<name>

# claim-2 loop (once scenarios exist)
python -m skillrace.revise_skill --skill-dir skills/<s> \
    --feedback out/campaign/<method>/<s>/campaign.json --out candidates/<method>-v2
python -m skillrace.skill_eval --scenario scenarios/<name> --skill-name <s> \
    --skill-dir candidates/<method>-v2 --out out/skill-eval/<method>-v2
```

Artifacts per campaign: `campaign.json` (per-iteration record: provenance,
violations, classification, seconds), `tree.json` + `tree.guards.json`
(skillrace), `cases/*/` (Dockerfile, candidate.json, checks/, validate.sh),
`runs/*/` (trace, episodes, verdicts, diff, cost).

## 4b. Paper, datasets, and prep (added later)

- **Paper** in `paper/` (`skillrace.tex` + verified `refs.bib`), acmart style, compiles
  clean; Figure 1 is a native editable TikZ pipeline. Placeholders are macros.
- **Dataset selection protocol** `docs/dataset-protocol.md` — pre-registered
  inclusion/exclusion criteria and reporting commitments (anti-cherry-picking).
- **D1 decision log** `candidates/skill-suite-candidates.md` — **code-behavior skills
  only** (I1 refined: the artifact must be code whose behavior is checkable; document-
  generation skills — docx/pptx/pdf/xlsx — are excluded under X1 as presentational).
  **28 skills authored — 24 base-build-verified offline + 4 build-deferred** (pip/npm
  deps): the 4 in-repo originals + 24 vendored verbatim from public repos (obra/superpowers,
  anthropics/knowledge-work-plugins, and the skillsmp S5 crawl), assembled instances-per-
  family (cli ×3, refactor ×3, sql ×4, unit-test ×2, parser ×2, plus singletons). Full
  grouped list: `candidates/D1-final-suite.md`. Fixed-invariant catalog + applicability
  matrices in `skills/INVARIANTS.md`. The S5 crawl produced 628 candidates → triaged
  (`skillrace.crawl_skillsmp` + `triage_candidates`). Honest ceiling: the pool cleanly
  yields ~28 *distinct* code-behavior skills (rest are forks/coupled/presentational); a
  literal 30+ needs broadening sources, not padding with near-duplicates.
- **D2 scenarios** `scenarios/` — 10 skill-generation scenarios × **10 hidden tests =
  100 tests, 192 facet-driven executable checks**. **Fully build-verified**: dedicated
  base `skillrace/skillgen-base` (`scenarios/build_base.sh`) built offline; every
  environment builds and all 192 checks run *inside the built containers* against
  hand-written reference solutions and pass (confirming none is vacuously unsatisfiable
  and every expected value is correct). Checks-per-test match real facets (rich
  scenarios ~2.6-3.0, pure-function ones ~1; no padding). Only the agent runs (an
  experiment) remain.
- **Offline test suite** `tests/test_pure.py` (18 tests, `pytest.ini`) over the pure
  functions — no Docker/network/model.
- **Prep tooling**: `skillrace.aggregate` (campaign.json → RQ1 table + LaTeX macros),
  `skillrace.calibrate` (segmentation F1 / merge kappa scorers, ready for labels),
  `scripts/run_suite.sh` (all methods × skills × greybox granularity sweep), and the
  `--regrade-k` reproducibility regrade in the loop.

## 5. What is left, in priority order

The pipeline, both datasets, the paper skeleton, and all analysis tooling are done.
What remains is mostly **running** it (compute), **labelling** (RQ2), and a few polish
items — none are design-blocked.

1. **Run the RQ1 campaigns (the headline numbers).** Execute the three rungs on the
   D1 suite under a fixed budget: `scripts/run_suite.sh` then `python -m skillrace.aggregate`,
   which prints the RQ1 table and the `\renewcommand` lines to paste into the paper.
   This is the single biggest remaining item — everything is plumbed, it just needs
   agent-run compute.
2. **Run the RQ3 skill-improvement study.** For each scenario: revise the base skill
   from each method's campaign findings (`revise_skill`), then `skill_eval` on the 100
   hidden tests. Do a **one-scenario dry run first** to confirm the reviser + eval loop
   end-to-end, then scale.
3. **Finish the D1 dataset to a literal 30** (optional; 28 is defensible). Build the 4
   dependency-gated skills (`pip`/`npm` in their `Containerfile.base`) on a networked
   machine, and/or re-run `crawl_skillsmp` with a GitHub-direct source. See
   `candidates/skill-suite-candidates.md`.
4. **Calibration labels (RQ2).** Hand-segment ~100 traces and label a merge-pair set,
   then run `python -m skillrace.calibrate` (scorers already written). Also compile-step
   verdict agreement on a hand-checked sample; tree build-stability across seed order.
5. **Loop parallelism** for real-scale budgets (iterations are sequential, ~4–6 min
   each). Random/greybox parallelize trivially; SkillRACE can batch several frontier
   targets per tree refresh. Report both per-run and wall-clock metrics.
6. **Greybox granularity sweep** at scale: `--greybox-level L0|L1|L2` per subset skill;
   pick the best level per skill for the headline (the driver already loops all three).
7. **Guard-extractor tuning**: prefer failure-signature/state conditions over
   file-identity ones (the observed `no_divergence` came from a behaviorally-inert
   module-name mutation). Consider batching guard extraction at larger tree sizes.
8. **Fill the paper** from `aggregate.py` output (RQ1 macros), add the real Figure 2
   (discovery-vs-budget plot), and clear the remaining `\todo`s.
9. **Deferred, recorded** (per build-plan): `agent_runtime` guards; the VeriGrey
   injection-oracle slice; cross-prefix tree merging (measure duplication first);
   in-process Pi SDK runner.

### What we did this session (changelog)

- Built Component 5 (guards + property-guided synthesis + agent-free validator), the
  campaign loop, the greybox rung, the pre-run oracle + fixed core, and the injection
  harness; wired and live-tested all three rungs end-to-end.
- Wrote the paper (`paper/skillrace.tex` + verified `refs.bib`, TikZ Figure 1).
- Built the dataset-selection protocol; crawled skillsmp (628 candidates) and triaged;
  assembled **D1 = 28 code-behavior skills** (24 built) with logged provenance, after
  refining I1 to exclude presentational/document skills.
- Built **D2 = 10 scenarios × 10 tests (100 tests / 192 checks)**, all build-verified
  and satisfiability-checked in-container.
- Added analysis tooling (aggregate / calibrate / crawl / triage / run_suite / k-regrade)
  and an offline unit-test suite; fixed several real bugs (see §3).

## 6. Known caveats to keep in mind (honesty box)

- `CLOSE_API_KEY` is visible inside the agent-under-test container
  (`run_case.py`); note it in the paper's setup or proxy the key.
- Timeout runs destroy the container → state checks are inconclusive for them;
  they count against the generating method identically for all rungs (the fixed
  core still judges them, incl. the budget property).
- The tree is a prefix tree: identical situations reached along different
  prefixes occupy separate nodes (path-tree semantics, like concolic execution).
  Defend explicitly or measure duplication before changing.
- Merge-cache keys include the node's broadened intent, so cache hit-rate
  drops as nodes grow — expect real (small) merge-call costs at 100+ runs.
- The `no-destructive-ops`-style compiled checks are only as nuanced as the
  compile prompt; keep auditing false positives/negatives and fold them into
  the calibration sample.
