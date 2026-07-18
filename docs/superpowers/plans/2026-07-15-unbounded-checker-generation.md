# Unbounded Checker Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove client output-token ceilings from generated checker calls, retry an unusable checker once, exclude a still-unusable property honestly, and validate the path with fresh GLM-4.7 and DeepSeek-V3.2 pilots.

**Architecture:** Extend the existing journaled `chat` request builder so `max_tokens=None` omits the provider field without bypassing receipts. Keep checker behavior inside `compile_checks.py`: one authoring attempt, one validation-guided retry, explicit exclusions, and one semantic audit over usable scripts only. Reuse the current campaign runner and development protocols for two budget-one pilots.

**Tech Stack:** Python 3.12, pytest, Yunwu OpenAI-compatible API, existing SkillRACE JSON receipts and campaign engine.

---

### Task 1: Allow an omitted output-token limit

**Files:**
- Modify: `skillrace/closeai.py`
- Test: `tests/test_closeai_journal.py`

- [ ] **Step 1: Write failing request-body tests**

Add tests that call `_chat_request_body_and_identity` with `max_tokens=None` for both the
chat-completions and responses payload shapes. Assert the serialized body omits
`max_tokens`/`max_output_tokens`, while identity contains `"max_tokens": null`. Retain an
integer case asserting the existing field remains present.

- [ ] **Step 2: Run the focused tests and observe RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_closeai_journal.py -k 'max_tokens or output_token'
```

Expected: the `None` cases fail because validation/request construction currently
requires an integer and serializes the field.

- [ ] **Step 3: Implement the minimal optional-token behavior**

In `_chat_request_body_and_identity`, add the provider token field only when
`max_tokens is not None`. In `chat`, accept `None` or an integer in `1..1_000_000`.
Preserve `max_tokens` in request identity and journal records as `null` so the omitted
choice remains reproducible.

- [ ] **Step 4: Run focused and journal regression tests**

```bash
.venv/bin/python -m pytest -q tests/test_closeai_journal.py
```

Expected: PASS.

### Task 2: Retry once and exclude unusable properties

**Files:**
- Modify: `skillrace/compile_checks.py`
- Modify: `skillrace/check_properties.py` only if active-property manifest reading needs adjustment
- Test: `tests/test_checker_semantic_audit.py`
- Test: `tests/test_check_isolation.py`
- Test: `tests/test_compile_identity.py`

- [ ] **Step 1: Write failing checker-flow tests**

Add tests proving:

```python
# usable first response
assert author_calls == 1

# invalid then valid
assert author_calls == 2
assert manifest["excluded_properties"] == []

# invalid twice alongside another valid property
assert manifest["active_property_ids"] == ["valid-property"]
assert manifest["excluded_properties"][0]["reason"] == (
    "checker_generation_failure"
)

# every property invalid twice
with pytest.raises(RuntimeError, match="no usable property checkers"):
    compile_case(...)
```

Also assert the audit input contains only active scripts and a semantic rejection becomes
`checker_semantic_rejection` without calling the rewrite helper.

- [ ] **Step 2: Run the checker tests and observe RED**

```bash
.venv/bin/python -m pytest -q \
  tests/test_checker_semantic_audit.py \
  tests/test_check_isolation.py \
  tests/test_compile_identity.py
```

Expected: failures because compilation currently stops at the first mechanical error and
semantic rejection invokes a rewrite.

- [ ] **Step 3: Implement the bounded retry/exclusion flow**

Change checker authoring to use `max_tokens=None`, `timeout_seconds=120`, and concise
prompt text. Restore one validation-guided retry, but never retry more than once. Store
both call summaries/costs and final script hash. After two invalid responses, append an
exclusion record and continue. Reject only when there are no active scripts.

Run the semantic audit once over active scripts. Convert rejected decisions to exclusion
records; do not call `rewrite_semantic_check`. Reject if no scripts remain. Include the
timeout policy, active IDs, exclusions, and author-call summaries in the compile
fingerprint/manifest and aggregate cost.

- [ ] **Step 4: Make checker execution honor active IDs**

If necessary, adjust `check_properties.py` to execute only manifest entries whose status
is active. It must never report excluded properties as holding or violated.

- [ ] **Step 5: Run focused regression tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_checker_semantic_audit.py \
  tests/test_check_isolation.py \
  tests/test_compile_identity.py \
  tests/test_campaign_engine.py \
  tests/test_campaign_outcomes.py
```

Expected: PASS.

### Task 3: Verify offline and run two fresh development pilots

**Files:**
- Create: `experiments/protocols/pilot.unbounded-checker.glm-4.7.runtime.json`
- Create: `experiments/protocols/pilot.unbounded-checker.deepseek-v3.2.runtime.json`
- Modify: `handoff.md`
- Modify: `docs/2026-07-14-session-handoff.md`
- Modify: `STATUS.md`
- Modify: `docs/implementation-status.md`

- [ ] **Step 1: Run complete offline verification**

```bash
.venv/bin/python -m pytest -q -m 'not live'
PYTHON=.venv/bin/python scripts/artifact_smoke.sh
.venv/bin/python -m compileall -q skillrace tests
git diff --check
```

Expected: all commands exit 0 and artifact smoke prints `PASS`.

- [ ] **Step 2: Create two bounded development protocols**

Create both files with these literal controls (the second file changes only the two
shown model-specific strings):

```json
{
  "schema": "campaign-protocol/1",
  "protocol_id": "skillrace-unbounded-checker-glm-4.7-v1",
  "status": "runtime",
  "model": "glm-4.7",
  "budget": 1,
  "bootstrap_count": 1,
  "max_generation_attempts_per_execution": 2,
  "seed_generator": {"batch_size": 1, "temperature": 0.9, "build_retries": 2},
  "greybox_level": "L1",
  "random_seed": 20260715,
  "repair": {
    "enabled": true,
    "timeout_seconds": 300,
    "max_output_tokens": 4000,
    "temperature": 0.0,
    "reasoning": true,
    "backend_by_method": {
      "random": "direct",
      "greybox": "direct",
      "skillrace": "pi"
    }
  }
}
```

```json
{
  "schema": "campaign-protocol/1",
  "protocol_id": "skillrace-unbounded-checker-deepseek-v3.2-v1",
  "status": "runtime",
  "model": "deepseek-v3.2",
  "budget": 1,
  "bootstrap_count": 1,
  "max_generation_attempts_per_execution": 2,
  "seed_generator": {"batch_size": 1, "temperature": 0.9, "build_retries": 2},
  "greybox_level": "L1",
  "random_seed": 20260715,
  "repair": {
    "enabled": true,
    "timeout_seconds": 300,
    "max_output_tokens": 4000,
    "temperature": 0.0,
    "reasoning": true,
    "backend_by_method": {
      "random": "direct",
      "greybox": "direct",
      "skillrace": "pi"
    }
  }
}
```

- [ ] **Step 3: Run one fresh pilot per model**

Use the next two manifest-ordered skills, new dated output directories, and never reuse
an earlier campaign or operation identity. Record proposal, realization, active/excluded
checker counts, semantic-audit outcome, agent-start count, terminal status, tokens, cache
reads, provider credits, and elapsed time.

- [ ] **Step 4: Stop safely on unknown outcomes**

If any call reaches `outcome_unknown` or leaves an unmatched intent, stop that campaign,
do not resume it, and record its accounting as unknown-nonzero-possible.

- [ ] **Step 5: Update authoritative handoffs**

Record implementation decisions, tests, both pilot outcomes, all known accounting,
indeterminate operations, remaining blockers, and the next safe action in `handoff.md`
and the dated handoff. Do not describe a development pilot as a headline result.

- [ ] **Step 6: Final verification**

```bash
.venv/bin/python -m pytest -q -m 'not live'
git diff --check
docker ps --format '{{.ID}} {{.Names}} {{.Status}}'
```

Expected: tests and whitespace checks pass; no SkillRACE campaign container is running.
