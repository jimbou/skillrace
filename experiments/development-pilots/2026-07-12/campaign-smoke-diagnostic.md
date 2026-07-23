# Dual-model campaign smoke: diagnostic run

**Date:** 2026-07-12  
**Classification:** development-only, incomplete, prohibited from headline reuse  
**Schedule:** `experiments/schedules/development-smoke.dual-model.json`  
**Schedule file SHA-256:** `209a3209f60d4e6b736fd25726c506f79402582e0b29d7234260da3e59fceab2`  
**Raw output:** `out/development-pilots/2026-07-12/campaign-smoke/`

## What happened

The five-cell smoke was launched with its original development setting
`epoch_size=4`. Yunwu connectivity worked for both selected models, but every completed
cell exhausted pre-agent realization/build attempts before Pi could start. Consequently
this run contains **zero counted agent executions, zero skill verdicts, and no result that
can compare methods**.

- GLM: 22 terminal journal events (21 successful provider responses and one timeout),
  21,238 recorded input tokens, 47,228 output/reasoning tokens, and 0.004203 Yunwu
  provider credits.
- DeepSeek: 14 terminal journal events (12 successful responses and two timeouts),
  13,517 recorded input tokens, 16,213 output/reasoning tokens, and 0.04293244 Yunwu
  provider credits.
- Four cells terminated as `aborted_pre_agent_attempt_cap`. The final DeepSeek SkillRACE
  cell was stopped once the run was known to be unusable, to avoid further spend.
- The forced stop leaves one development-only journal intent without a terminal event:
  operation `d24a32a975954c9c8f9b89dd383333ff`. It must never be retried or promoted.

## Generic defects exposed and changes made

1. Failed realization responses and build diagnostics were collapsed to the string
   `realization/build failed`. The generator now persists the exact bounded reason and
   preserves provider cost even when parsing/validation fails after a paid response.
2. A parallel epoch assigned fresh `eNNNN-a00` coordinates after pre-agent failures,
   bypassing the promised `a00..a04` retries and making strict campaign verification
   impossible. Frozen headline campaigns now fail closed unless `epoch_size=1`.
3. Headline throughput now comes from independent cells: RQ1 uses up to six cell workers
   under the shared 4/3/3 API/Docker/agent pool; RQ3 uses three internally sequential
   scenario workers after a two-worker preparation barrier. This retains concurrency
   without changing adaptive state or retry accounting.

## Required rerun

Run a fresh output root and fresh development ledger after focused tests pass. The next
run's newly preserved diagnostic must first identify whether the universal realizer
failure is response schema, generated-tail policy, Docker build, or repair behavior. Fix
only the cross-skill contract/prompt/parser issue, then repeat the five-cell smoke. Do not
delete, resume, or relabel this diagnostic run.

