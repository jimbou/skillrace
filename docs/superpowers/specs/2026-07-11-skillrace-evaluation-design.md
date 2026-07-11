# SkillRACE Evaluation and Reliability Design

**Date:** 2026-07-11

**Status:** Approved direction; implementation requires a separate plan.

## Goal

Make SkillRACE a reliable, scalable tool and produce a defensible ISSTA evaluation in
which SkillRACE, an unguided random baseline, and a VeriGrey-inspired baseline receive
the same agent model, seed-generation distribution, agent-run budget, runner, basic input
sanity gate, and property checker.

The primary research objective is practical: maximize confirmed skill-defect yield per
expensive agent execution. Reasoning-derived guards guide search toward promising
unexplored situations; they are not exact symbolic constraints, and a generated test is
valuable even when it misses its intended branch but discovers a different defect.

## Headline Claim

Under a fixed agent-run budget, reasoning- and property-guided behavioral exploration
finds more confirmed skill defects than unguided LLM mutation and tool-sequence-guided
greybox mutation.

The paper will describe SkillRACE as concolic-inspired approximate exploration. It will
not require exact path reproduction or claim that every reasoning statement is causal.

## Experimental Contract

For each skill and campaign replication:

1. Use one frozen seed-generation prompt, model configuration, realization pipeline,
   build policy, and sanity gate for every method.
2. Generate a pre-campaign pool large enough to allocate an equal-size seed set to each
   method. Randomly assign seeds to methods in blocks so the sets may differ while coming
   from the same distribution. Record the pool, allocation, and hashes before campaigns.
3. Give each method the same total number of agent executions, including seeds.
4. Use the same configured model for the agent, generation, segmentation, merging,
   guard extraction, selection, synthesis, and check compilation.
5. Use the same runner, basic candidate sanity gate, and property checker.
6. Keep method-specific search state in isolated output directories.
7. Report model tokens, cost, wall time, invalid candidates, fallbacks, timeouts, and
   inconclusive checks in addition to the agent-run-normalized headline metric.

A default campaign has 20 allocated seed executions and 100 method-specific executions,
for 120 agent executions per method. Pilot studies may use smaller budgets, but a budget
is fixed before comparing methods. Campaign replications use newly generated and newly
randomized seed pools from the same frozen protocol. This exercises robustness to seed
choice instead of relying on one favorable initial corpus.

Identical seeds are not required for the headline comparison. To quantify seed variance,
a smaller preregistered sensitivity experiment reruns the three methods from one matched
seed set. If the headline ordering changes materially under matched seeds, both results
are reported and the effect is not attributed solely to the search method.

## Shared Candidate Sanity Gate

Every method receives the same non-semantic pre-run checks:

- the container builds;
- required workspace files and tools exist;
- the task can be invoked;
- the starting task is not already solved when this is mechanically decidable;
- the candidate and its checks have valid schemas and syntax.

Build or schema failure is a generation failure, not an agent execution. Generation
failure rates and time remain reported. SkillRACE may additionally validate its proposed
condition because condition-directed validation is part of the technique; the baselines
do not receive reasoning, guards, or properties for generation.

## Search Methods

### Unguided random baseline

The random baseline mirrors a black-box LLM fuzzer:

- choose a candidate from its seed corpus;
- ask the shared model for an unguided task/environment mutation;
- pass it through the shared realization, build, repair, and sanity pipeline;
- add successful mutants to its corpus without behavioral feedback;
- never read traces, reasoning, episodes, properties, or tree state when selecting the
  next mutation.

### VeriGrey-inspired baseline

The greybox baseline adapts VeriGrey's feedback mechanism to skill correctness testing:

- read only schematized tool-call sequences;
- retain candidates that add a new tool, transition, or full sequence;
- assign one energy unit for each of those three novelty signals;
- mutate a selected seed with the shared model, conditioned on its tool sequence;
- use the shared realization, build, repair, sanity gate, runner, and property checker;
- never read reasoning, episodes, outcomes, guards, tree state, or correctness
  properties during generation.

Because injection-specific context bridging and the injection oracle do not transfer,
the paper calls this method "VeriGrey-inspired" rather than VeriGrey itself.

Tool-event granularities L0, L1, and L2 are evaluated on development skills excluded
from the final comparison. One global level is frozen for the headline experiment; all
three levels are reported as a sensitivity analysis. The headline never selects a
different best level per evaluated skill.

### SkillRACE

SkillRACE builds episodes and a behavior tree, extracts reasoning/outcome-derived guards,
selects property-relevant unexplored situations, synthesizes a new task/environment, and
validates the proposed condition when possible.

Mutation is deliberately opportunistic rather than minimally causal:

- it may change multiple environment features when that creates a meaningful unexplored
  case;
- intended branch reach is recorded but is not required;
- every property violation counts regardless of which branch exposed it;
- branch misses, incidental new branches, and fallbacks remain in the tree and influence
  future exploration;
- synthesized candidates must still satisfy the shared sanity gate and any explicit
  condition predicate supplied by SkillRACE.

Each generated case records its motivating guard, intended mutation, targeted property,
validation result, actual branch classification when available, and whether a discovered
violation was targeted or serendipitous.

## Ablations

The headline comparison contains random, VeriGrey-inspired, and full SkillRACE. The
following mechanism ablations run on a preregistered representative subset:

- SkillRACE with uniform-random frontier selection;
- SkillRACE without reasoning, using observable outcomes only;
- direct property-guided LLM generation without episodes or a tree;
- a model-strength ablation that swaps the single shared model consistently across the
  agent and every model-driven pipeline role.

These ablations test which component explains yield without turning every ablation into
a full-suite cost burden.

## Correctness Repairs Required Before Campaigns

The implementation must first:

- preserve Pi's actual exit status instead of allowing post-run diff collection to mask
  it;
- freeze the seed-generation protocol and implement recorded blocked allocation from a
  pre-campaign pool;
- make check caches depend on complete property, candidate, environment, prompt-version,
  and model hashes;
- use one-based runs-to-first-violation and handle campaigns with no violation as
  right-censored;
- wire per-skill applicability matrices into property selection;
- clean candidate and compile images after their last consumer;
- persist infrastructure errors without treating them as successful agent runs;
- add resumable, atomic campaign records.

## RQ3: Skill Generation and Revision Experiment

RQ3 tests whether feedback produced by each testing method improves an LLM-generated
skill on independently authored hidden tasks. It is a separate experiment built on the
same runner, methods, sanity gate, and oracle infrastructure as RQ1.

### Scenario package

Each of the ten scenarios contains five disjoint artifact groups:

1. `scenario.md`: the target skill purpose and public task contract;
2. `base_skill/`: the zero-shot generated skill plus generation provenance;
3. `campaign/`: properties and environment-generation configuration visible to the
   testing methods;
4. `tests/`: hidden prompts, starting environments, and executable pass criteria visible
   only to the final evaluator;
5. `expert_skill/`: an independently authored upper-bound skill used only for evaluation.

The zero-shot base skill is generated once from the scenario purpose using the shared
model. Its exact generation prompt, response, model configuration, timestamp, and content
hash are stored. Existing base skills without recoverable generation provenance are
regenerated rather than retrospectively labelled as model-generated.

### Phase 1: Produce testing feedback

For every scenario and campaign replication:

1. Start random, VeriGrey-inspired, and SkillRACE from the same base skill.
2. Allocate equal-size seed sets using the RQ1 randomized seed protocol.
3. Give each method the same agent-run budget to test the base skill.
4. Run the scenario's campaign properties on every generated case.
5. Regrade property violations and preserve replayable cases, checks, traces, and method
   search summaries.

The campaign process cannot read `scenarios/<name>/tests/`. Filesystem-level tests assert
that neither the generators nor the reviser receive a path or copied content from the
hidden directory.

### Phase 2: Normalize feedback and revise

Each method's output is projected into the same bounded feedback envelope:

- confirmed findings with property, task, environment, and reproduction evidence;
- explored-situation summary;
- method-specific useful evidence, such as tool novelty for greybox or guard/mutation
  context for SkillRACE;
- inconclusive findings clearly separated from confirmed findings.

The envelope schema, ordering, and maximum token count are identical across methods;
missing fields are empty rather than omitted. The reviser receives the same system
prompt, base skill, model, temperature, and output budget for all conditions. Only the
feedback envelope differs. It produces three revised skills: random-feedback,
greybox-feedback, and SkillRACE-feedback. The zero-shot skill is not revised.

### Phase 3: Hidden evaluation

The evaluator runs six conditions on all ten hidden tests for the scenario:

- no skill, measuring the agent's native capability;
- the zero-shot base skill;
- the random-feedback revision;
- the VeriGrey-feedback revision;
- the SkillRACE-feedback revision;
- the expert skill as a separately reported upper bound.

Every condition receives byte-identical hidden prompts, containers, checks, agent model,
and wall-clock budget. Each hidden test is repeated three times to measure stochastic
pass probability. A run passes a hidden test only when every functional criterion holds;
fixed-invariant violations are reported both separately and in a strict all-properties
pass metric.

The primary RQ3 outcome is the change in hidden-test pass probability from the zero-shot
skill to each revised skill. Secondary outcomes are stable 3/3 pass rate, strict
all-properties pass rate, testing-plus-revision cost, and improvement by scenario.
Analysis is paired by hidden test and clustered by scenario and campaign replication.

### Required benchmark repairs

Before the skill-generation study:

- correct all text-template prompts to specify `{{key}}` consistently;
- make `json-csv/t5` require exit zero, output creation, and valid CSV behavior;
- make error, timeout, and performance checks verify that the intended artifact exists
  and the command actually ran;
- strengthen fix-failing-test integrity checks with initial hashes and detection of
  deletion, rename, skip, harness override, and assertion weakening;
- store reference solutions, validation logs, image digests, and a machine-readable
  manifest proving that every check accepts its reference and each test rejects an empty
  or deliberately incorrect implementation where applicable.

The RQ3 orchestrator writes one manifest linking the base-skill hash, campaign allocation,
feedback-envelope hash, revised-skill hash, hidden-test hashes, run IDs, and aggregate
results for every condition. This manifest is the input to the paper's RQ3 table and
plots.

## Metrics and Analysis

The primary metric is distinct confirmed defect yield per agent execution. A confirmed
defect is a reproducible property violation grouped by skill, property, and equivalent
failure cause; raw property IDs alone are not treated as unique defects.

Secondary metrics are:

- discovery curve and area under that curve;
- agent executions to first confirmed defect, with right censoring;
- runs containing any violation;
- reproducibility frequency over three reruns of the same case;
- unique behavioral branches;
- intended-branch, different-new-branch, no-divergence, and path-miss rates;
- targeted versus serendipitous defects;
- candidate validation, rejection, repair, and fallback rates;
- fixed versus compiled-oracle findings and inconclusive rates;
- model tokens, dollars, CPU time, and wall clock.

Results are reported per skill and by skill family. Statistical uncertainty is computed
with skill-family-aware resampling or a hierarchical model so related CLI, SQL, parser,
and refactoring skills are not treated as independent observations.

## Parallel Execution

Parallelism is allowed where it does not create shared mutable search state:

- skills, methods, model ablations, and replications run concurrently;
- independent property checks compile concurrently;
- D2 test/skill-version/replication combinations run concurrently;
- random and greybox candidate runs may be queued independently within resource limits;
- SkillRACE uses bounded epochs: freeze tree version N, synthesize and run a small diverse
  batch, then fold completed results in deterministic candidate-ID order into version
  N+1.

A single reducer owns each tree and campaign manifest. Workers write immutable per-case
and per-run directories. Docker, CPU, and API concurrency use explicit semaphores.

## Dataset and Reporting Boundaries

The public mined D1 skills form the headline dataset after all deferred images build.
Locally authored skills are reported separately as controlled case studies. Results are
clustered by family and include low-contingency cases rather than silently excluding
them.

The artifact contains a locked environment, a sub-30-minute smoke test, frozen datasets,
raw campaign records, reference-oracle evidence, analysis scripts, and commands that
reproduce every paper table and figure.

## Acceptance Criteria Before Full-Scale Runs

Full campaigns do not begin until:

1. runner exit-status regression tests pass;
2. all methods demonstrably use equal-size seed sets allocated from the same recorded
   seed pool and frozen generation protocol;
3. random and greybox tests prove they cannot access reasoning, properties, or tree data;
4. all D2 checks pass syntax, reference-solution, empty-solution, and targeted mutation
   tests;
5. a pilot covering at least one debugging, CLI, parser, SQL, and low-contingency skill
   completes without missing artifacts or unrecoverable infrastructure failures;
6. the pilot reports branch-classification, fallback, oracle-inconclusive, cost, and
   candidate-rejection rates;
7. the final protocol, models, seeds, budgets, skills, properties, and analysis are frozen
   before headline results are inspected.
