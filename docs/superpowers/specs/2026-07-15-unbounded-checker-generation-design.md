# Unbounded checker generation with one retry

**Date:** 2026-07-15  
**Status:** approved design, pending implementation

## Goal

Let Yunwu finish generated property checkers without a client output-token ceiling while
keeping the experimental path simple, time-bounded, and honest when a usable checker
cannot be produced.

## Request behavior

Checker authoring, semantic audit, and semantic rewrite calls omit `max_tokens` from the
Yunwu request. The shared chat wrapper accepts `max_tokens=None`, includes that choice in
the durable request identity and journal, and does not serialize either `max_tokens` or
`max_output_tokens` into the provider payload. Existing callers that pass an integer are
unchanged.

Each generated-checker call has a 120-second whole-call deadline. The authoring prompt
explicitly asks for only a concise Bash script, asks the model to finish quickly, and
discourages unnecessary reasoning. This is identical for every method and model.

## One retry, then exclude

For each applicable property:

1. request one checker;
2. run Bash syntax and existing mechanical safety validation;
3. if unusable, make exactly one retry that includes the validation error and previous
   response;
4. if the retry is also unusable, retain both call receipts and hashes, mark the property
   `excluded_checker_generation_failure`, and continue compiling later properties.

An excluded property is neither a pass nor a failure. It is absent from the candidate's
active checker denominator and cannot contribute a detected defect. The manifest records
the original property set, active property set, excluded property IDs/reasons, script
hashes, operation receipts, tokens, cache reads, costs, and timeouts. If every property is
excluded, the candidate is rejected before agent execution because it has no usable
oracle.

## Semantic audit

The single pre-run semantic audit receives only the usable checker set. A semantic
rejection does not start another rewrite loop in this simplified path; that checker is
marked `excluded_checker_semantic_rejection`. Malformed or timed-out audit output rejects
the candidate because none of its semantic decisions are trustworthy.

## Fairness and accounting

The behavior is shared unchanged by Random, VeriGrey-inspired, and SkillRACE. Exclusions
are reported per candidate and per property so methods cannot benefit silently from
missing checks. Every attempt and retry uses the existing durable Yunwu journal and is
included in token, cache, cost, fingerprint, and reproducibility records. Provider
outcome-unknown still terminates the campaign without retry.

## Validation runs

After offline tests pass, run two fresh development-only, budget-one, epoch-one pilots:

- one with `glm-4.7`;
- one with `deepseek-v3.2`.

Use new output and operation identities, do not resume earlier campaigns, do not tune
prompts per skill or method, and do not treat these development pilots as headline data.

## Tests

Tests must first fail and then cover:

- `max_tokens=None` omits the provider field while remaining part of request identity;
- existing integer-token callers remain unchanged;
- checker calls use the 120-second deadline and concise prompt;
- a usable first response makes one call;
- an invalid first response and usable retry make two calls;
- two invalid responses exclude the property without fabricating pass/failure;
- all properties excluded rejects the candidate before agent execution;
- semantic audit sees only active scripts and semantic rejection excludes that checker;
- manifests/fingerprints include exclusions, calls, usage, costs, and timeout policy; and
- outcome-unknown still stops the campaign.
