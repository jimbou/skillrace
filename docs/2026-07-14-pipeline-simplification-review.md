# Pipeline simplification review — 2026-07-14

## Bottom line

The experiment has a sound small core, but implementation accumulated development
compatibility, generalized crash recovery, parallel modes that the frozen design does
not use, and two different repair pipelines. We should not rewrite all of that before
obtaining the next bounded result. The practical sequence is:

1. finish the minimal checker self-audit;
2. pass the offline suite;
3. run the one already-requested bounded patch/replay chain;
4. simplify the high-cost headline path before launching thousands of runs; and
5. stop adding freeze, compatibility, and documentation layers.

## Actual experiment path

```text
schedule
  → candidate proposal
  → realization/build repair
  → sanity check
  → pre-run checker authoring + semantic self-audit
  → counted agent execution
  → isolated checker execution
  → failure grouping and unchanged-skill confirmation
  → skill patch
  → patched exact replay
  → receipt-verified analysis
```

RQ3 adds one base-skill generation, three public campaigns, equal-sized feedback
projection, three skill revisions, and the fixed hidden exam. Those are study steps, not
implementation accidents.

## Keep

These directly support a paper claim and are worth their complexity:

- one shared candidate/checker/runner path for all three methods;
- the 30 started-agent budget and clear pre-agent rejection boundary;
- pre-run checker creation and the new one-call semantic self-audit;
- fresh networkless final-state execution for each checker;
- unchanged-skill confirmation of suspected groups;
- patch followed by an independent exact replay;
- durable model usage/cost receipts, especially for paid calls with uncertain outcomes;
- Random/VeriGrey/SkillRACE information boundaries;
- RQ3 public/hidden separation and equal feedback byte limits; and
- top-level parallelism across independent cells.

## Simplify or remove, in priority order

### 1. Confirm/group before patching every failure

**Current:** RQ1 and RQ3 can patch and replay every raw failed execution, while
unchanged-skill confirmation later operates once per normalized failure group. The
documented absolute bound adds up to 7,200 patch/replay agent executions.

**Simpler:** group raw failures first, replay one unchanged representative, and only if
that group reproduces, patch and exactly replay that same representative once.

**Why:** this preserves the conservative defect definition and paired methods while
removing duplicate patches for repeated instances of the same failure. It is the largest
cost and runtime reduction available. Make this one small protocol change after the
bounded gate and before headline execution.

### 2. Use one repair implementation in RQ1 and RQ3

**Current:** RQ1 uses `patch_only.py` plus `patch_confirmation.py`; RQ3 still routes
through the older combined machinery in `repair_validation.py`. Analysis supports both
formats.

**Simpler:** use patch-only plus exact confirmation for both studies, then delete the
combined production path after old development artifacts are archived as diagnostics.

**Why:** one repair definition, one receipt shape, and one validator are easier to run
and explain.

### 3. Remove within-cell parallel epochs

**Current:** `campaign_engine.py` and `generator.py` retain proposal epochs, batch
reservations, reverse-completion folding, and recovery for `epoch_size > 1`. Frozen
headline schedules explicitly use `epoch_size=1` because adaptive methods must fold each
result before choosing the next case.

**Simpler:** make sequential within-cell execution the only production mode. Keep the
existing top-level worker pool across independent cells.

**Why:** removes a large unused state/recovery branch without reducing experiment
parallelism that is scientifically valid.

### 4. Batch initial checker authoring

**Current:** one authoring model call is made per property, followed by one audit call.
Most candidates have three or four properties.

**Simpler:** ask for all property scripts in one structured authoring call, then retain
the separate one-call audit and at most one rewrite for each rejection.

**Why:** reduces ordinary checker compilation from roughly four or five calls to two.
Do this only after the minimal audit path has passed its bounded gate; it is a cost
optimization, not required for checker validity.

The July 14 bounded pilots supplied a smaller immediate simplification: the legacy paid
mechanical-correction call was removed and the single authoring call received enough
output room. This avoids repeating a truncated/empty response while preserving
fail-closed validation. Batch authoring remains optional future work, not a prerequisite
for the next bounded gate.

### 5. Remove post-hoc checker authoring from production code

**Current:** `check_properties.py` still exposes `--author-post-hoc` for old debugging
artifacts even though such verdicts are inadmissible.

**Simpler:** keep old artifacts readable by an offline compatibility command if needed,
but remove post-run authoring from the experiment executor.

**Why:** eliminates an unsafe path from the component that produces admissible verdicts.

### 6. Collapse duplicated attempt lifecycle files

**Current:** an attempt can have proposal, intent/start, external terminal, cleanup
intent, cleanup, receipt, fold, and campaign copies containing overlapping fields.

**Simpler:** preserve the paid-call journal and started-agent marker, but consolidate
local execution into one append-only attempt event file plus one final receipt.

**Why:** the fairness requirements need durable state transitions, not many copies of the
same result. This is lower priority because changing recovery code immediately before a
run carries risk.

### 7. Hardcode and retain only the final two model tracks

**Current:** development support and catalogs cover many GLM, Qwen, DeepSeek, and Grok
variants.

**Simpler:** once the final two routes are selected, keep their exact catalogs, images,
rates, and preflights in the headline path. Move other model support to a clearly marked
development archive rather than maintaining it as current experiment machinery.

**Why:** route flexibility helped development but is not part of the paper experiment.

### 8. Use one lightweight freeze manifest

**Current:** several draft suite, protocol, schedule, image-lock, recursive provenance,
and promotion concepts overlap.

**Simpler:** after the bounded pilot, write one final manifest containing hashes of the
protocols, 30-skill suite, 10 scenarios, schedules, model catalogs/rates, images, code
revision, and analysis configuration.

**Why:** one inspectable identity file is enough for a local experiment and matches the
user's requested lightweight freeze.

### 9. Consolidate status documentation

**Current:** `STATUS.md`, `docs/implementation-status.md`, the reviewer guide, dated
handoffs, and several plans repeat the same state.

**Simpler:** keep `STATUS.md` as the current truth, one dated handoff as the detailed
log, and stable component docs. Historical plans remain history and need no ongoing
synchronization.

**Why:** reduces stale contradictions and maintenance time without changing code.

### 10. Stop validating development artifacts as headline inputs

**Current:** many old pilots, compatibility readers, and diagnostic schemas remain near
the active path because they were useful while debugging.

**Simpler:** preserve them read-only under development output, but make the final entry
scripts accept only the one current protocol/receipt version.

**Why:** old evidence remains auditable without expanding every production validator.

## What not to simplify

- Do not remove the pre-run checker boundary to save a call.
- Do not count a single generated-checker violation as a defect without confirmation and
  successful patched exact replay.
- Do not share adaptive feedback with Random or extra trace information with VeriGrey.
- Do not replace exact token/cost accounting with estimates after calls have run.
- Do not expose RQ3 hidden tests to public campaigns or revisions.

## Recommended action boundary

Before the next bounded paid chain, make no further architectural change beyond the
minimal checker audit. The chain is intended to reveal whether the current core can now
produce one defensible end-to-end result.

Before the full headline experiment, implement only priorities 1–3 and the lightweight
freeze in priority 8. Priorities 4–7 and 9–10 are cleanup/optimization and can wait until
they block cost, reliability, or artifact preparation.
