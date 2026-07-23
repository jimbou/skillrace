# Bounded Development Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute and verify one fresh development-only failure through proposal, agent, checker, repair replay, confirmation, and analysis.

**Architecture:** Add an explicit bounded-development confirmation capability while leaving the headline default strict. Add a separate verifier/report for short engineering campaigns, then run it against a fresh Yunwu-backed schedule.

**Tech Stack:** Python 3.11, pytest, Docker, Pi 0.73.1, Yunwu OpenAI-compatible API.

---

### Task 1: Explicit bounded confirmation

**Files:**
- Modify: `skillrace/rq3_confirmation.py`
- Modify: `skillrace/experiment_driver.py`
- Test: `tests/test_rq3_confirmation.py`
- Test: `tests/test_experiment_driver.py`

- [x] Write tests proving short campaigns reject by default and run only with the explicit bounded-development capability.
- [x] Run those tests and confirm the expected failures.
- [x] Add the optional manifest confirmation mode and propagate it to the confirmation primitive.
- [x] Record actual bounded search count and `development_only: true` without changing headline ledgers.
- [x] Run both test modules and confirm they pass.

### Task 2: Development artifact analysis

**Files:**
- Create: `skillrace/development_gate.py`
- Create: `tests/test_development_gate.py`

- [x] Write a failing test for a deterministic report joining campaign, repair, and confirmation evidence.
- [x] Write failing cases for missing failures, missing executions, and mismatched source hashes.
- [x] Implement recursive ledger validation and deterministic report output.
- [x] Run the development-gate and existing strict RQ1 analyzer tests.

### Task 3: Fresh live gate

**Files:**
- Create: `experiments/schedules/development-gate.v32.json`
- Modify: `tests/test_development_pilot_schedule.py`
- Create after run: `experiments/development-pilots/2026-07-13/bounded-development-gate.md`

- [x] Add a one-worker V3.2 development schedule with repair and bounded confirmation enabled.
- [x] Verify provider connectivity and the immutable development image before launch.
- [x] Run into a fresh `out/development-pilots/2026-07-13/` root.
- [x] Require at least one definite failure; otherwise use another fresh bounded cell rather than fabricating a failure.
- [x] Run `python -m skillrace.development_gate` and inspect every linked receipt.
- [x] Document exact model, counts, terminal outcomes, tokens, cost, hashes, and exclusions.

### Task 4: Final no-cost verification and cleanup

**Files:**
- Modify: `docs/superpowers/plans/2026-07-12-pre-experiment-closure-roadmap.md`

- [x] Run focused tests, compilation, the offline artifact smoke, and `git diff --check`.
- [x] Mark the live implementation gate complete only if the report passes.
- [x] Confirm no experiment, agent, Docker-build, timeout, or checker process/container remains.

### Task 5: One post-timeout cell and live-derived corrections

- [x] Bound the shared realization/build/repair transaction at 300 seconds and allow one
  provider attempt per generation step.
- [x] Run exactly one new predeclared SkillRACE/V3.2/json-parser cell and stop after it.
- [x] Verify one raw failed execution, two unchanged-skill confirmation jobs, and the real
  bounded RQ1 row. One signature reproduced; the Pi patch timed out before editing, so
  confirmed yield remained zero.
- [x] Constrain Pi patching to `edit,write` and compact saved trace episodes so the
  32,000-byte RQ1 envelope retains all seven v6 episodes offline.
- [ ] On a future day, validate those post-v6 patcher corrections with one fresh,
  non-reused failure before declaring the positive patch/replay gate closed.
