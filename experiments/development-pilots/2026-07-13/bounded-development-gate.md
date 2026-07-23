# Bounded post-fix development gate

**Date:** 2026-07-13  
**Classification:** development-only engineering evidence; prohibited from headline reuse  
**Model:** `deepseek-v3.2` through Yunwu  
**Method/skill:** Random / `json-parser`  
**Schedule:** `experiments/schedules/development-gate.v32.json`  
**Output:** `out/development-pilots/2026-07-13/bounded-gate-v1/`

## Result

The machine gate passed and exercised the complete requested path:

`proposal → agent → checker → patch → exact replay → unchanged-skill confirmation → analysis`

The campaign contained two counted executions and four candidate attempts. Two candidates
were correctly rejected before the agent, one counted execution passed all seven checks,
and one counted execution produced a definite `valid-json-out` violation. That failure
received exactly one patch of the original skill and one exact-case replay. One grouped
representative was then rerun against the unchanged original skill. The deterministic
analysis report records all four phase-coverage flags as true.

The patch replay and unchanged-skill confirmation both reached their 300-second limit.
Their terminal statuses are therefore `timeout`; the finding was neither reproduced nor
repair-validated and contributes zero headline defects. This is a successful plumbing
gate, not evidence that Random found a real skill defect and not an effectiveness result.

## Live accounting

- Search: 2 agent executions; 1 run with a definite property violation.
- Repair: 1 patch call and 1 patched-skill exact-case replay; status `timeout`.
- Confirmation: 1 unchanged-skill exact-case replay; status `timeout`.
- Analysis: 1 recursively validated development-gate report; status `passed`.
- All four Pi runs contain native thinking blocks on every assistant turn: 18, 19, 19,
  and 18 turns respectively.
- Pi execution receipts total 83,379 input, 33,744 output, and 473,216 cache-read tokens,
  costing 1.214422 Yunwu credits.
- The 15 direct proposal/realization/check-compilation calls total 17,034 prompt, 11,395
  completion, and 6,912 cached-input tokens, costing 0.068253 credits.
- The repair patch call cost 0.011313 credits. Total gate cost excluding the tiny
  connectivity preflight was 1.293988 credits; the preflight cost 0.000035 credits.

## Cross-cutting defects found and fixed

1. Short development campaigns previously forced a zero-work confirmation ledger. An
   explicit `bounded-development` capability now permits real confirmation only for a
   development-only manifest and non-frozen embedded protocol. Default/headline behavior
   still requires exactly 30 executions.
2. A relative experiment output caused saved case/run paths to contain the cell root and
   then be prefixed with that root again during repair. New driver roots are absolute.
   Existing workspace-relative spellings are accepted only when the existing target
   resolves inside the same campaign root. The completed search was not repeated.
3. The development analyzer initially assumed verdicts were embedded in `campaign.json`.
   Real campaigns store the immutable verdict list separately and retain its terminal
   oracle status/count in the campaign. The analyzer now accepts either representation;
   recursive repair/confirmation validators still hash-check the external evidence.

## Immutable evidence hashes

- Schedule: `479800009c1fc3d6f899916cff6df46c9fc2b69851eac5a2ab812a0d09859296`
- Campaign: `7fc516cdf8a4895f9440dcc14294ef722cad38871a6af5bfe00c40b23359b7ff`
- Repair ledger: `ad650124f14f3fadf07b1ab7151c67d0e8ec2e7b2c997fa2c2afdf16351d2db4`
- Confirmation ledger: `24764aa4079b15317d1b64200be0078457eab36b52d9269eb8c918fef2b8cd90`
- Development analysis: `3bd5b14974e39e2d2e9a2ad7ec820afefc0c1abc625b12c4286d1d3eae8ec0e5`

The development report is
`out/development-pilots/2026-07-13/bounded-gate-v1/analysis/development-gate.json`.
