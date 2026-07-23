# Yunwu Provider Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every future SkillRACE model and agent call through Yunwu using the
canonical `yunwu_key` environment variable. The early documented spelling
`yumwu_key` remains a direct-client compatibility alias only.

**Architecture:** Keep the existing journaled OpenAI-compatible client as the single transport boundary, but change its production endpoint and required secret to Yunwu. Align the Pi image, runner, isolation allowlists, diagnostics, and user-facing runbooks so direct calls and agent-under-test calls use the same provider.

**Tech Stack:** Python 3.12 stdlib HTTP client, pytest, Docker, Pi coding agent, JSON.

---

### Task 1: Lock down direct-call provider selection

**Files:**
- Modify: `tests/test_closeai_journal.py`
- Modify: `skillrace/closeai.py`

- [x] **Step 1: Write the failing test** — set only `yumwu_key`, mock `urlopen`, call `chat`, and assert that the request goes to `https://yunwu.ai/v1/chat/completions` with a bearer token.
- [x] **Step 2: Run the focused test and verify it fails because the legacy key is required.**
- [x] **Step 3: Change the production URL, secret lookup, and provider-facing names to Yunwu while retaining the journal interface.**
- [x] **Step 4: Re-run the focused journal tests.** The repository virtual environment lacks pytest, so a direct mocked-transport execution verified the same request path.

### Task 2: Route Pi agent runs through Yunwu

**Files:**
- Modify: `tests/test_runner_status.py`
- Modify: `skillrace/run_case.py`
- Modify: `skillrace/gen_agent.py`
- Modify: `skillrace/segment_agent.py`
- Modify: `images/pi-base/models.closeai.json`
- Modify: `images/pi-base/Dockerfile.pi-base`
- Modify: `images/pi-base/run_once.sh`

- [x] **Step 1: Write assertions for Pi provider name `yunwu` and secret name `yumwu_key`.**
- [x] **Step 2: Run the runner tests and verify legacy CloseAI configuration fails those assertions.**
- [x] **Step 3: Update the Pi model configuration and all runner command/secret propagation paths.**
- [x] **Step 4: Re-run the focused runner tests.** The affected journal, runner, isolation, provenance, and artifact tests pass (115 tests).

### Task 3: Preserve experimental isolation and operational clarity

**Files:**
- Modify: `skillrace/rq3_isolation.py`
- Modify: `skillrace/rq3_pipeline.py`
- Modify: `skillrace/skill_eval.py`
- Modify: `skillrace/candidate_policy.py`
- Modify: `REQUIREMENTS.md`
- Modify: `images/pi-base/README.md`
- Create: `scripts/yunwu_hello.py`

- [x] **Step 1: Update isolation/secret allowlists to carry only `yumwu_key` and update diagnostics to identify Yunwu.**
- [x] **Step 2: Add a one-call diagnostic that uses the same journaled client and prints only non-secret status/output.**
- [x] **Step 3: Update the active operational runbooks to document `yumwu_key` and the Yunwu endpoint.**
- [x] **Step 4: Run the relevant isolation, runner, and artifact smoke tests.** The affected suite passes (115 tests) and `PYTHON=python3 scripts/artifact_smoke.sh` passes.

### Task 4: Verify the provider end to end

**Files:**
- Verify: `scripts/yunwu_hello.py`

- [x] **Step 1: Confirm `yunwu_key` is set without printing it.** It is exported by the shell configuration.
- [x] **Step 2: Make exactly one minimal `gpt-4o` completion request via the journaled diagnostic.** The response was HTTP 200 and returned the requested test message.
- [x] **Step 3: Report HTTP/model success or the sanitized error, then run the affected offline test suite.** `qwen3.6-flash` is not advertised by Yunwu and received HTTP 503; `qwen3.6-plus` is advertised, completed successfully, and Pi recorded a `thinking` block. The affected suite passes (116 tests) and the offline artifact gate passes.

### Follow-up gate before headline experiments

- [ ] Freeze one advertised model for every condition. The current protocol still
  names `qwen3.6-flash`, which Yunwu does not advertise; `qwen3.6-plus` is the
  validated closest replacement but has not yet replaced the protocol/model defaults.
- [ ] Capture and archive Yunwu's dated input/output rate-card entry for the chosen
  model, then update the Pi model configuration and Python accounting table from that
  snapshot. The authenticated `/v1/models` record contains no price, so no rate is
  inferred or silently copied from CloseAI.
