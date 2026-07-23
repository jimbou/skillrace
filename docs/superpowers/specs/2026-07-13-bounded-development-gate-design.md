# Bounded development gate design

**Status:** approved by the user's explicit request to execute the remaining gate  
**Scope:** development validation only; no headline result eligibility

## Purpose

Run one fresh, inexpensive campaign that proves the real pipeline can traverse proposal,
agent execution, property checking, per-failure skill patching, exact-case replay,
grouped unchanged-skill confirmation, and machine analysis. The gate must exercise at
least one definite public failure. Its observations are engineering diagnostics and may
never be combined with RQ1 results.

## Design

The experiment manifest gains an optional confirmation mode:
`bounded-development`. It is valid only when the manifest and every participating
campaign are explicitly development-only. The default mode remains `headline`, so
existing frozen schedules and their 30-execution requirement are unchanged.

The confirmation primitive accepts a short campaign only when its caller passes an
explicit bounded-development capability and the embedded campaign protocol is not
frozen. Its ledger records the actual search execution count and a
`development_only: true` marker. Without that capability, the primitive continues to
reject every campaign that is not exactly 30 executions.

A separate `skillrace.development_gate` verifier recursively validates the terminal
campaign, repair ledger, and confirmation ledger. It checks source hashes and accounting,
requires at least one raw failed execution, one patch-and-exact-replay record, and one
unchanged-skill confirmation, and joins confirmation clusters to their representative
repair outcomes. It writes a deterministic `development-gate.json`. It does not calculate
headline effect sizes or call the strict RQ1 analyzer.

## Failure handling

Missing, incomplete, hash-mismatched, or zero-failure artifacts fail closed. A repair or
confirmation may end in any declared terminal outcome; the engineering gate verifies
that the phase executed, not that the model fixed or reproduced the defect. Provider
unknown outcomes remain non-retryable under existing journals. The run uses a fresh
development output root and is never resumed from earlier diagnostic roots.

## Verification

Tests must prove that default short-campaign confirmation still rejects, explicit
bounded development confirmation executes, headline manifests cannot select the mode,
and the development report rejects missing phase evidence. After unit/integration tests,
one real Yunwu run must create and validate the full artifact chain. All run processes and
containers must be absent at handoff.
