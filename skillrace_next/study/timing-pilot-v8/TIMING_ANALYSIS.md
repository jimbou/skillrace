# Frozen weak-agent timing decision

The full study uses one 60-second wall-clock cutoff for weak task execution and exact
task replay. The cutoff is identical for DeepSeek v4 Flash, Qwen 3.6 Flash, S0, Random,
VeriGrey, SkillRACE, Part I, and Part II. It was selected on 2026-07-21 before the full
study configs or results existed.

## Included evidence

The distribution contains 23 unique, completed `skillrace-run-record/1` runs from four
terminal pilot cells:

- timing-pilot-v6 DeepSeek Part I `file-check`: 6 development runs;
- timing-pilot-v6 Qwen Part I `file-check`: 6 development runs;
- timing-pilot-v8 DeepSeek Part II `fix-failing-test`: 2 development and 4 held-out
  runs; and
- timing-pilot-v8 Qwen Part II `fix-failing-test`: 1 development and 4 held-out runs.

Durations are calculated directly as `ended_at - started_at`. Duplicate copies of one
run record under verifier input are counted once by `run_id`.

| Population | n | Minimum | Median | p90 | p95 | Maximum | Mean |
|---|---:|---:|---:|---:|---:|---:|---:|
| All | 23 | 6.886s | 16.548s | 28.347s | 31.620s | 33.287s | 17.321s |
| DeepSeek v4 Flash | 12 | 12.381s | 18.651s | 31.319s | 32.566s | 33.287s | 20.311s |
| Qwen 3.6 Flash | 11 | 6.886s | 11.686s | 28.067s | 28.242s | 28.417s | 14.060s |

The 60-second cutoff is 26.713 seconds above the observed maximum. It is a simple fixed
operational limit rather than an outcome-dependent threshold.

## Exclusions

The distribution excludes:

- every interrupted or terminal-failed timing-pilot root;
- invalid or malformed proposed tests that never reached weak execution;
- Docker build and environment-validation time;
- provider proposal, seed planning, and test-materialization time;
- Terra checker-authoring and Docker checker-execution time; and
- S0 generation and patch-authoring time.

In the completed Qwen Part II cell, five of six development slots were invalid: two
Random slots, one VeriGrey slot, and two SkillRACE slots. They remain scientific missed
slots in the campaign summary but contribute no weak-execution duration. One VeriGrey
development run and all four held-out runs were valid timing samples.

## Separate role limits

Only `timeouts.pi` is frozen to 60 seconds in the full-study configs; exact replay uses
that same task-execution limit. Docker build, checker execution, Terra authoring,
proposal/model calls, and patch authoring retain their separate role-specific limits.
The cutoff must not be changed after inspecting a full-study success, failure, timeout,
or patch decision.
