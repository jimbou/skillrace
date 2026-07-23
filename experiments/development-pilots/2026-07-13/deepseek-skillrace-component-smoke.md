# DeepSeek SkillRACE component smoke

**Date:** 2026-07-13  
**Classification:** development-only; excluded from headline analysis  
**Schedule:** `experiments/schedules/development-smoke.deepseek-skillrace.json`  
**Model/method/skill:** `deepseek-v4-flash` / SkillRACE / `json-parser`

## Successful component run (v5)

`out/development-pilots/2026-07-13/deepseek-skillrace-v5/` completed its two-run
development budget. Both candidates passed the shared realization/build/sanity path,
started and completed Pi, and produced durable run, trace, cost, candidate, fold, cleanup,
and campaign receipts.

- Counted agent executions: 2 of 2; attempts: 2; schedule and campaign status:
  `completed`.
- Bootstrap run: one normal SkillRACE fold was recorded.
- Exploration run: the fold had no feasible property/guard frontier at this deliberately
  tiny one-bootstrap budget, so SkillRACE correctly recorded `skillrace-fallback` rather
  than claiming a fabricated targeted mutation.
- Fixed trace/safety checks held on both runs. No suspected property failure or repair was
  produced. The development-only confirmation ledger correctly records zero confirmation
  executions because a two-run smoke is ineligible for the 30-run headline confirmation
  protocol.

This is an engineering component test, not a comparison with Random or VeriGrey-inspired
L1 and not evidence about defect yield.

## Checker defect discovered and corrected

All three generated state-property checks were initially inconclusive. The cause was
Docker configuration inheritance, not an agent or skill outcome: `run_case` keeps the
agent container alive with `--entrypoint /bin/sleep`; `docker commit` preserves that
entrypoint; the isolated checker then launched the snapshot as `sleep sleep 300`, so the
child exited before the script could be staged.

`skillrace.check_properties.check_container_command` now explicitly sets
`--entrypoint /bin/sleep`. `tests/test_check_isolation.py` starts the source container with
the same override; the focused regression was observed failing under the prior
implementation and now passes while also checking snapshot isolation and timeout cleanup.
This correction is generic to all methods and skills.

## Fresh preflight after the fix (v6)

`out/development-pilots/2026-07-13/deepseek-skillrace-v6/` made three allowed
pre-agent proposal attempts and then terminated as `aborted_pre_agent_attempt_cap` with
zero counted agent executions. The durable journal records HTTP 429 for each attempt.

Independent direct requests then received the same Yunwu response for all of:

- the documented provider-minimal DeepSeek request;
- the pipeline's non-thinking request; and
- an otherwise identical thinking-enabled request.

Yunwu described the condition as current-group upstream saturation. This establishes a
provider preflight failure rather than a payload, candidate, Docker, checker, or method
failure. The output must not be resumed, relabelled, or used as a result. Retry the smoke
only from a new development output root after DeepSeek direct preflight succeeds.
