# Supported Yunwu Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reusable Yunwu and Pi support for `glm-4.5-air` and `glm-4.7` without changing current headline model selection.

**Architecture:** Separate runtime support from experiment selection in `model_policy`, then route general-purpose tools through the supported inventory while leaving freeze-specific code on the selected experiment inventory. Add one-model Pi catalogs and preserve fail-closed production pricing.

**Tech Stack:** Python 3.12, pytest, Bash, Docker, Pi coding agent, Yunwu OpenAI-compatible API.

---

### Task 1: Model inventory and request behavior

**Files:**
- Modify: `tests/test_yunwu_model_freeze.py`
- Modify: `tests/test_closeai_journal.py`
- Modify: `skillrace/model_policy.py`
- Modify: `skillrace/closeai.py`

- [x] Add failing tests asserting the complete supported inventory, unchanged headline inventory, and native GLM thinking-disable payloads for both new models.
- [x] Run the focused tests and confirm they fail because the new inventory/constants do not exist.
- [x] Add the two model constants, `GLM_MODELS`, `SUPPORTED_MODELS`, and supported-model validation; use `GLM_MODELS` for request construction.
- [x] Run the focused tests and confirm they pass.

### Task 2: Pi catalogs and generic command support

**Files:**
- Create: `images/pi-base/models.yunwu.glm-4.5-air.json`
- Create: `images/pi-base/models.yunwu.glm-4.7.json`
- Modify: `images/pi-base/build.sh`
- Modify: `images/pi-base/run_once.sh`
- Modify: `scripts/yunwu_hello.py`
- Modify: `scripts/yunwu_hello_cost.py`
- Modify: `tests/test_yunwu_model_freeze.py`

- [x] Add failing tests requiring one-model catalogs and supported-model CLI/build allowlists.
- [x] Run the focused tests and confirm missing catalogs/allowlist failures.
- [x] Add the catalogs and make generic tools accept `SUPPORTED_MODELS`; keep unknown prices explicit rather than indexing a missing rate.
- [x] Run the focused tests and confirm they pass.

### Task 3: Development protocol and live verification

**Files:**
- Modify: `tests/test_protocol_authority.py`
- Modify: `skillrace/campaign_protocol.py`

- [x] Add failing protocol tests separating supported, agent-capable, and selected experiment models.
- [x] Implement the agent-capability runtime rule and run focused protocol tests.
- [x] Run the complete affected test set and Python compilation.
- [x] Run minimal live Yunwu calls and bounded Pi probes for both models; record that GLM 4.7 produced structured tools while GLM 4.5 Air produced textual pseudo-calls.
