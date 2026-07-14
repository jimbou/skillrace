# Minimal Pre-run Checker Semantic Audit

**Date:** 2026-07-14  
**Status:** approved for implementation

## Goal

Prevent generated property checkers from turning unsupported assumptions into apparent
skill failures, while keeping the existing experiment pipeline simple. The change must
remain pre-run and identical for Random, VeriGrey-inspired, and SkillRACE.

## Scope

This change extends the existing checker compilation path in
`skillrace/compile_checks.py`. It does not introduce a separate auditor model, service,
protocol role, semantic-contract framework, or static-analysis subsystem.

The audit addresses five failure classes:

1. requirements not supported by the task prompt;
2. guessed artifact interfaces or callable signatures;
3. conditional properties enforced when their precondition is absent;
4. missing required artifacts treated as vacuous success; and
5. checkers that manufacture, copy, or echo the expected output instead of observing the
   agent's artifact.

## Compilation flow

For each candidate, the compiler continues to author one Bash checker per applicable
property and apply the existing syntax and execution-policy validation. It then makes
one fresh semantic self-audit call over the complete set of checker scripts. The call
uses the same model already assigned to checker compilation for that experiment track.
It receives only pre-run information: the task prompt, properties, initial environment
summary, and generated scripts. It never receives an agent trace, final workspace, or
verdict.

The audit returns a small JSON object with exactly one decision per property:

```json
{
  "checks": [
    {"property_id": "p1", "decision": "accept", "reason": "supported"},
    {"property_id": "p2", "decision": "reject", "reason": "guesses an unstated callable"}
  ]
}
```

The compiler validates the response mechanically: it must be JSON, contain the exact
property ID set once each, use only `accept` or `reject`, and provide bounded reasons.
Malformed or incomplete audit output rejects the candidate before agent execution.

Each rejected checker receives at most one targeted rewrite call containing the task,
property, rejected script, and audit reason. The rewritten script must pass the existing
Bash syntax and execution-policy checks. If it does not, checker compilation rejects the
candidate before agent execution. The system does not silently omit the property and
does not spend an agent-execution slot on an unjudgeable candidate.

This is deliberately described as a semantic **self-audit**, not independent validation.
It is a bounded safeguard against the concrete observed failures, not a claim that an
LLM can prove its own checker correct.

## Identity, accounting, and artifacts

The existing compile fingerprint gains the semantic-audit prompt version and audit
policy version. A cached checker set is reusable only when those versions and all current
compile inputs match.

The existing `checks/manifest.json` records:

- the audit prompt and policy versions;
- the original script hashes;
- one audit decision and reason per property;
- whether a targeted rewrite occurred;
- the final script hashes;
- audit and rewrite token usage;
- audit and rewrite provider-credit cost; and
- the redacted model-call receipt identities already produced by the Yunwu journal.

Audit and rewrite costs are included in the existing aggregate compile cost. No new
ledger or receipt system is added.

## Tests

Implementation follows red-green-refactor cycles. The first regressions reproduce the
two saved json-parser failures:

1. reject a checker that turns the conditional `IF the parser emits JSON` property into
   an unconditional JSON-stdout requirement and can echo the supplied input itself; and
2. reject a checker that guesses common function names, ignores the actual
   `parse_sensor_data` callable, and invokes a selected function with an assumed
   incompatible argument.

Additional focused tests cover a fully accepted checker set, exact audit property-ID
coverage, one-rewrite enforcement, fail-closed invalid rewrites, fingerprint changes,
and token/cost/receipt recording. Existing compile identity and checker-isolation tests
remain the regression boundary.

After focused and no-live tests pass, an offline suite audit inventories all property
specifications for the 30 RQ1 skills and 10 RQ3 public scenarios and scans saved generated
checker patterns. No paid model or agent call occurs until that audit is reviewed.

## Follow-up simplification review

Before any paid run, perform a read-only end-to-end review of candidate generation,
realization, sanity, checker compilation, agent execution, checking, confirmation,
repair, replay, analysis, and documentation. Classify each gate and artifact as:

- **keep:** directly protects fairness, budget accounting, or result reproducibility;
- **simplify:** useful purpose implemented with unnecessary layers or duplicate records;
- **remove:** historical, redundant, or unused by the current paper design.

Prefer deleting or collapsing machinery over introducing abstractions. Any proposed
behavioral simplification must preserve the three-method information boundary, pre-run
checker timing, counted-execution definition, exact replay requirement, and auditable
token/cost totals. Review findings will be documented separately so they do not expand
the checker fix.

## Non-goals

- proving checker correctness formally;
- adding a second auditor model;
- optimizing prompts for a particular skill or method;
- guaranteeing that SkillRACE outperforms a baseline;
- changing headline experiment allocation or statistical analysis; or
- rerunning or reusing prior paid development operations.
