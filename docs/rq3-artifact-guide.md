# RQ3 artifact guide

RQ3 asks a simple question: does feedback from a testing method help the same model
write a better skill? It compares four skill versions on the same ten hidden tests:

1. the original zero-shot skill;
2. a revision using Random's feedback;
3. a revision using the VeriGrey-inspired baseline's feedback;
4. a revision using SkillRACE's feedback.

## What happens in one scenario

`python -m skillrace.rq3_pipeline run ...` performs the complete sequence:

1. It physically copies only `scenario.md`, `base_skill/`, and `campaign/` into a
   public stage. The hidden `tests/` directory is absent.
2. It runs Random for 30 fresh executions, VeriGrey-inspired for 10 bootstrap plus
   20 guided executions, and SkillRACE for 10 bootstrap plus 20 guided executions.
3. Every raw public failure receives one independent original-skill patch and exact-case
   replay. SkillRACE's patcher receives its bounded native reasoning/tree/guard context;
   baseline patchers receive the common bounded failure core. Repairs are recorded but
   never enter the aggregate feedback envelope.
4. It groups suspected defects by property and normalized failure signature. One
   representative from each group is rerun once. These confirmation executions and
   their costs are recorded separately and never consume the 30-execution budget.
5. It projects each method's confirmed findings, explored situations, and useful
   search evidence into the same ordered envelope. The frozen cap is exactly 3,600
   canonical-JSON UTF-8 bytes, not a mislabeled tokenizer estimate. Deterministic
   section round-robin allocation prevents generic explored-case text from consuming
   the whole envelope before method-specific evidence is considered.
6. It makes one revision call per feedback condition with the same track model,
   temperature, prompt template, and output limit. Only the envelope differs.
7. Only after all public work passes a hidden-content audit does it open the ten
   hidden tests. Every condition runs each test exactly once through the same runner
   and hidden-independent executable checks.
8. It recursively verifies the resulting manifest and every linked start, result,
   receipt, campaign, cost, feedback, revision, and hidden-evaluation hash.

## How failures are counted

The headline denominator is all ten scheduled tests. A timeout, execution error,
inconclusive oracle, or missing result is therefore a non-pass; it is not silently
dropped. The artifact also reports an available-case sensitivity rate and the exact
status counts so readers can see whether infrastructure affected the result.

The primary outcome is the paired pass-rate change from zero-shot to each revision.
SkillRACE-versus-Random and SkillRACE-versus-VeriGrey contrasts are secondary. Effects
are aggregated by scenario; the 100 hidden tests are not treated as 100 independent
top-level samples.

## Crash and resume rule

Every model or agent execution gets a durable start record before the external call
and a terminal result plus receipt afterward. Completed artifacts are never executed
again. If a process dies after the external action may have started but before any
terminal evidence was committed, the tool reports an unknown external outcome and
stops. It does not silently spend a second call and pretend exactly-once execution.

## Base-skill provenance gate

A headline run requires the zero-shot skill's generation prompt, raw response, frozen
model configuration, hashed provider identities, actual provider token counts,
cost, hashes, start record, and terminal receipt. The ten historical checked-in skills
currently carry honest `regeneration-required` markers because their original model
calls did not retain this evidence. They must be regenerated before expensive RQ3
campaigns; the orchestrator intentionally fails closed until then.

The model-track driver does not overwrite those templates. It creates a private prepared
scenario beneath the track result root and makes exactly one generation call for each
scenario/model pair. Thus the two complete tracks contain twenty base-generation calls.
The normalized benchmark-template hash—covering all hidden tests, references, mutants,
and public campaign inputs while excluding only the intentionally different base
skill—must be identical across tracks.

For a development-only single-package check:

```bash
python -m skillrace.rq3_base generate \
  --scenario-id <name> \
  --purpose scenarios/<name>/scenario.md \
  --out /tmp/<model>/<name>/base_skill \
  --model glm-4.5-flash
```

The headline driver performs the copy, hash binding, validation, and exactly-once resume
automatically; manual replacement of `scenarios/<name>/base_skill/` is forbidden.

## Main commands

```bash
scripts/run_model_track.sh glm-4.5-flash rq3
scripts/run_model_track.sh deepseek-v4-flash rq3
```

The checked-in protocols and schedules remain `draft` until the final artifact freeze.
Development or draft results must not be reported as headline data.
