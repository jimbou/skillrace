# GLM 4.5 and Qwen3 Coder Flash Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support `glm-4.5` and `qwen3-coder-flash` according to their observed Yunwu streaming, tool, and reasoning capabilities.

**Architecture:** Extend the existing model inventories with an explicit reasoning-trace capability. Add a strict SSE-to-response adapter for GLM 4.5 while retaining the existing synchronous path for other models, then add one-model Pi catalogs and generic CLI/build support.

**Tech Stack:** Python 3.12, urllib, JSON/SSE, pytest, Bash, Docker, Pi, Yunwu.

---

### Task 1: Capability inventory

**Files:**
- Modify: `tests/test_yunwu_model_freeze.py`
- Modify: `tests/test_protocol_authority.py`
- Modify: `skillrace/model_policy.py`
- Modify: `skillrace/campaign_protocol.py`

- [x] Add failing tests for both supported/agent models, GLM-only reasoning-trace eligibility, and unchanged headline selection.
- [x] Run the focused tests and confirm missing constants/capability behavior.
- [x] Add model constants and `REASONING_TRACE_MODELS`; require that inventory for a full runtime campaign protocol.
- [x] Run the focused tests and confirm they pass.

### Task 2: Strict GLM streaming direct client

**Files:**
- Modify: `tests/test_closeai_journal.py`
- Modify: `skillrace/closeai.py`

- [x] Add failing tests for GLM 4.5 streaming request bytes, valid SSE aggregation, and malformed/incomplete stream rejection.
- [x] Run the tests and confirm the synchronous-only client fails them.
- [x] Add `STREAM_ONLY_MODELS` request selection and a strict SSE aggregation helper used before existing response validation.
- [x] Run the transport and journal tests and confirm they pass.

### Task 3: Catalogs, commands, and live probes

**Files:**
- Create: `images/pi-base/models.yunwu.glm-4.5.json`
- Create: `images/pi-base/models.yunwu.qwen3-coder-flash.json`
- Modify: `images/pi-base/build.sh`
- Modify: `images/pi-base/run_once.sh`
- Test: `tests/test_yunwu_model_freeze.py`

- [x] Add failing catalog and allowlist assertions.
- [x] Add the one-model catalogs and accept both identifiers in generic image helpers.
- [ ] Run focused and full tests, compilation, shell syntax, JSON validation, and diff checks.
- [x] Build both images and run bounded Pi artifact probes; record reasoning/tool behavior and ensure no containers remain.
