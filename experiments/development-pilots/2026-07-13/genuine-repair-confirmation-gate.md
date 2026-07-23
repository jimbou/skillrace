# Genuine failure-to-repair gate audit

**Date:** 2026-07-13  
**Classification:** development-only; prohibited from headline-result reuse  
**Development model:** `deepseek-v3.2` through Yunwu  
**Final gate status:** the complete machinery was exercised and verified, including a
fresh post-timeout SkillRACE cell with one independently reproduced failure, but no
`repair_confirmed` defect was produced. Full experiments must not be described as
positively gated yet.

## What the positive gate requires

One counted public search execution must fail a definite executable check. An independent
rerun with the unchanged skill must reproduce that failure. A patcher may edit only the
original `SKILL.md`, without running the test. A later independent execution must replay
the exact same case and checks with the patched skill and pass every originally failed
property. Only then may RQ1 count one `repair_confirmed` defect.

This is stricter than merely showing that the pipeline launches. It prevents flaky agent
behavior, checker errors, and plausible-looking patches from being counted as skill bugs.

## Strongest complete live chain

The strongest auditable chain is rooted at:

`out/development-pilots/2026-07-13/positive-gate-v3/deepseek-v3.2/json-parser/random/`

The corrected-evidence patch and its replay are rooted at:

`out/development-pilots/2026-07-13/positive-gate-v4/`

The chain behaved as follows:

1. Random proposed a BIM JSON parsing/validation task. One earlier candidate was rejected
   by sanity before the agent, so it did not consume the execution budget.
2. One coding-agent execution completed and failed `parses-valid` and `rejects-invalid`.
3. Unchanged-skill confirmation ran once per mechanically distinct failure cluster.
   `parses-valid` reproduced; `rejects-invalid` did not reproduce.
4. The first direct patch had received checker errors but not the saved workspace diff or
   executable checker scripts. Its exact replay returned `same_failure`.
5. The evidence extractor was fixed and tested. A new immutable patch received the task,
   environment, required input paths, hash-identified workspace-diff excerpt, exact
   checker messages, and hash-identified head/tail excerpts of both failed checker scripts.
6. The corrected-evidence patch added general rules for JSON decode errors, required-field
   validation, stable output creation, exit codes, and stderr diagnostics. It did not run
   or inspect the failure while patching.
7. A later independent exact replay still returned `same_failure`.
8. The real bounded RQ1 verifier joined and recursively checked the campaign, original
   confirmations, patch receipt, replay receipt, raw run manifests, costs, verdicts, and
   hashes. It counted zero confirmed defects.

The final verified RQ1 row is:

`out/development-pilots/2026-07-13/positive-gate-v4/rq1-verified-cell.json`

Its key values are:

- search budget: 1 counted agent execution;
- raw failed executions: 1;
- raw failure observations: 2;
- original confirmations: 1 `confirmed`, 1 `not-reproduced`;
- corrected patched replay: `same_failure`;
- repair validation: 1 `reproduced-but-not-repaired`, 1 `not-reproduced`;
- confirmed events: 0;
- search cost: 0.513030 Yunwu credits;
- unchanged-skill confirmation cost: 1.224691 credits;
- corrected patch plus exact replay cost: 0.504017 credits;
- verified-chain inclusive cost: 2.241738 credits.

The RQ1 verifier recorded the campaign artifact hash
`2c54b0125587c6d934dbd5324007e9fdf20848d06078ff560d0f9e268e8f1b1d`
and all linked file hashes in the verified row.

## Other live branches and why they do not qualify

### Earlier reproducible Random failure

`out/development-pilots/2026-07-13/genuine-repair-confirmation-gate-v6/`

The unchanged original skill reproduced the `valid-json-out` failure, but the final
substantive patch replay returned `same_failure`. Its bounded RQ1 analysis therefore also
counted zero defects. Earlier immutable attempts in `v1` through `v5` preserve entrypoint,
delimiter, cosmetic-patch, journal-identity, and timeout diagnostics; they are not result
evidence.

### Fresh SkillRACE argparse cell

`out/development-pilots/2026-07-13/positive-gate-v1/`

The one counted execution passed all checks. No failure was fabricated and no patch was
launched.

### Fresh SkillRACE network-validation cell

`out/development-pilots/2026-07-13/positive-gate-v2/`

The search run failed only `fixed-terminated-within-budget`. The unchanged-skill replay
did not reproduce it, so it is not a skill defect. This branch also exposed a missing
`skillrace/pi-base:0.73.1-deepseek-v3.2` image: the patch attempt failed before any model
tokens or cost, and patched replay was correctly skipped. The missing image was later
built and networklessly audited with Pi 0.73.1 and the one-model `deepseek-v3.2` catalog.

### Historical incomplete functional campaign

`out/development-pilots/2026-07-13/v32-two-cell-v3/`

An explicit compatibility loader preserved the exact hash of its old repair-less,
development-only runtime protocol. Resume recovered existing receipts without another
provider call. However, the second reserved execution was
`external-outcome-indeterminate` and lacks proof that the agent started. Strict RQ1
analysis must reject it; the campaign was not relabeled or weakened into valid evidence.

### Final fresh SkillRACE JSON attempt

`out/development-pilots/2026-07-13/positive-gate-v5/`

Provider-backed realization exceeded twelve minutes while the campaign still had zero
attempts and zero counted executions. The development driver was terminated, and no test,
patch, or result was claimed. This exposes a remaining need for an effective hard timeout
around proposal realization.

### Post-timeout SkillRACE JSON gate

`out/development-pilots/2026-07-13/positive-gate-v6/`

This was the one fresh predeclared cell run after the timeout correction. Both initial
realizations completed. One candidate then spent too long rebuilding a repaired
Dockerfile and was cut off at the shared 300-second realization/build deadline; the
other candidate started one counted Pi execution. The run failed two executable
properties, `parses-valid` and `valid-json-out`.

The unchanged original skill was independently executed once for each distinct failure
signature. `parses-valid` reproduced and `valid-json-out` did not. The Pi patcher received
the original skill and bounded failure evidence, but spent its 300-second limit using
read/grep calls and never edited `SKILL.md`. Fail-closed handling recorded one patch
attempt with `status=error`, zero patched replay executions, and zero confirmed defects.
No retry or replacement cell was launched.

The real bounded RQ1 verifier recursively accepted the artifacts at
`out/development-pilots/2026-07-13/positive-gate-v6/rq1-verified-cell.json`. Its key
values are one counted search execution, one raw failed execution, two raw failure
observations, one reproduced cluster, one non-reproduced cluster, one
`patch_not_completed` repair status, and zero confirmed events. Search cost was
0.407537 Yunwu credits, unchanged-skill confirmation cost was 0.446574 credits, and the
verified inclusive cost was 0.854111 credits. The schedule completed with observed
resource peaks of one API, one Docker, and one agent slot.

## Implementation corrections made during the audit

1. Future campaign results now copy the authoritative `run.json` run ID. Explicit
   bounded-development analysis may recover a missing historical duplicate from the
   hash-checked run manifest; default/headline analysis remains strict.
2. The RQ1 verifier now uses the shared contained-path resolver for historical
   workspace-relative campaign paths.
3. Direct and Pi patch prompts reject cosmetic-only edits and request actionable,
   general procedural guidance. Response normalization removes only unambiguous
   prompt-owned wrappers.
4. When structured campaign fields are absent, common repair evidence now recovers:
   required input paths from `candidate.json`, a complete-file hash plus bounded
   head/tail workspace-diff excerpt, and complete-file hashes plus bounded head/tail
   excerpts of the failed executable checker scripts. Explicit recorded fields still
   take precedence.
5. Old repair-less protocols can be loaded only through an explicit
   development-only/runtime resume method. Their raw bytes and canonical hash remain
   unchanged; normal and headline parsing still requires the repair policy.
6. The missing `deepseek-v3.2` Pi patch image was built and audited.
7. Shared candidate realization now has one 300-second transaction deadline covering
   initial realization, Docker builds, and Dockerfile repair calls. Each generation
   model step gets one provider attempt, and build/model timeouts are clipped to the
   transaction time remaining. The v6 cell demonstrated the deadline in production.
8. Pi patching now exposes only `edit,write`; the complete skill and evidence are already
   embedded in the first prompt, so inspection tools can no longer consume the entire
   patch budget before the required blind edit. This is prompt version
   `skillrace-pi-patch/4`.
9. Saved SkillRACE episodes are compacted deterministically with native opening
   reasoning, ordered tool spans, bounded/hash-identified arguments and results, and
   episode outcomes. The RQ1 repair envelope is now 32,000 canonical-JSON bytes rather
   than the unrelated 3,600-byte RQ3 revision-feedback cap. On the v6 evidence, this
   retains all seven reasoning episodes instead of silently retaining zero.
10. Pi patch usage is now snapshotted before forced timeout cleanup and reconciled with
    any post-cleanup session. This prevents a teardown from erasing already-spent token
    and cost evidence. The historical v6 patch receipt remains immutable and therefore
    truthfully retains its observed zero-usage accounting anomaly.

## Verification

- Focused protocol, campaign, evidence, patcher, confirmation, and RQ1 tests: passed.
- Full `python -m pytest -q`: passed (with only the repository's expected skips).
- `python -m compileall -q skillrace tests`: passed.
- Changed schedule JSON parsing and `bash -n images/pi-base/build.sh`: passed.
- `git diff --check`: passed.
- Final process/container audit: no experiment driver, agent, patcher, run container, or
  deferred cleanup shell remained.

## What remains before full experiments

The positive practical gate is still open. The generation timeout, trace retention, and
edit-only Pi patch corrections are implemented and offline-tested, but the edit-only
patcher has not yet completed a fresh live genuine-failure patch and exact replay. Any
future closure attempt must use a new predeclared cell and fresh output root; it must not
repatch either saved JSON failure above. Success requires a genuine functional failure,
unchanged-skill reproduction, one blind completed patch, a passing exact replay, and a
nonempty `confirmed_events` entry from the real RQ1 verifier.
