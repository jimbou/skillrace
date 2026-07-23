# Guided Pi Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the opaque inline-evidence Pi repair invocation with a bounded read→reason→edit workflow that gives Pi the complete saved failure evidence without allowing test execution or replay.

**Architecture:** Python stages one read-only `repair-context.json` and a writable copy of the original skill, then launches a small Node runner against the Pi SDK already installed in the pinned container. The runner enables only `read`, `grep`, `edit`, and `write`, monitors turns and tool calls, and applies a path policy: Pi must inspect the skill and evidence before making one `SKILL.md` mutation. Python remains responsible for structural validation, accounting, cleanup, and the immutable patch receipt.

**Tech Stack:** Python 3.12, pytest, Docker, Node.js 20, Pi coding-agent SDK 0.73.1.

---

### Task 1: Freeze the guided invocation contract

**Files:**
- Modify: `tests/test_pi_patcher.py`
- Modify: `skillrace/pi_patcher.py`

- [x] **Step 1: Write the failing test**

Change the primary patcher test to require one combined evidence mount, a short prompt containing only the two required paths, SDK-runner configuration for `read,grep,edit,write`, medium thinking, and a bounded turn count. Assert that neither the original skill body nor diagnostic reasoning is duplicated into the initial prompt.

- [x] **Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_pi_patcher.py::test_pi_patcher_stages_guided_read_then_single_edit`

Expected: FAIL because the current patcher embeds the complete payload and exposes only `edit,write`.

- [x] **Step 3: Write minimal implementation**

Refactor `make_pi_patcher()` to stage:

```text
/workspace/SKILL.md             writable, original skill copy
/evidence/repair-context.json   read-only, common plus method evidence
/runtime/guided_patch.mjs       read-only SDK runner
```

Pass a short prompt, `PI_ALLOWED_TOOLS=read,grep,edit,write`, `PI_THINKING_LEVEL=medium`, and `PI_MAX_TURNS=10`. Preserve the existing network, model-catalog, timeout, accounting, cleanup, and post-patch validation boundaries. Enforce two distinct direct reads before mutation, block early grep and duplicate reads, and expose the remaining-read and blocked-call counts in compact diagnostics.

- [x] **Step 4: Run test to verify it passes**

Run: `pytest -q tests/test_pi_patcher.py`

Expected: all focused patcher tests pass.

### Task 2: Implement the SDK path and mutation policy

**Files:**
- Create: `images/pi-base/guided_patch.mjs`
- Modify: `tests/test_pi_patcher.py`

- [x] **Step 1: Write the failing policy test**

Add a static/runtime contract test that requires the runner to load the pinned global Pi package, disable resource discovery, select the requested Yunwu model, enable only the four approved tools, reject reads outside the skill/evidence paths, reject writes outside `SKILL.md`, reject mutation before both required reads, reject a second mutation, and abort after the configured turn bound.

- [x] **Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_pi_patcher.py -k guided_runner`

Expected: FAIL because `guided_patch.mjs` does not exist.

- [x] **Step 3: Write minimal implementation**

Create the Node runner using `createAgentSession`, `DefaultResourceLoader`, `AuthStorage`, `ModelRegistry`, `SessionManager`, and `SettingsManager`. Use an inline extension to enforce path/order/one-mutation policy and a session subscription to emit bounded diagnostic events and abort on the turn limit.

- [x] **Step 4: Verify syntax and focused tests**

Run:

```bash
node --check images/pi-base/guided_patch.mjs
pytest -q tests/test_pi_patcher.py
```

Expected: syntax check exits 0 and all focused tests pass.

### Task 3: Verify accounting and failure visibility

**Files:**
- Modify: `tests/test_pi_patcher.py`
- Modify: `skillrace/pi_patcher.py`

- [x] **Step 1: Write failing accounting tests**

Require usage extraction from Pi's generated session filename inside the accounting directory, compact terminal diagnostics for timeout/error results, and deletion of raw session/event files after extraction.

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_pi_patcher.py -k 'usage or timeout or diagnostics'`

Expected: at least one failure because the current code reads only `accounting/session.jsonl` and loses timeout detail.

- [x] **Step 3: Implement minimal accounting support**

Aggregate usage over session JSONL files while excluding the SDK event log. Return turn/tool counters and the final event kind as non-semantic operational diagnostics, then remove accounting and Pi-home directories exactly as before.

- [x] **Step 4: Run focused tests**

Run: `pytest -q tests/test_pi_patcher.py tests/test_patch_only.py tests/test_campaign_protocol.py`

Expected: all tests pass.

### Task 4: Synchronize the repair design and run bounded live gates

**Files:**
- Modify: `docs/superpowers/specs/2026-07-13-configurable-patch-only-repair-design.md`
- Modify: `docs/pi-integration.md`

- [x] **Step 1: Update documentation**

Document the combined repair context, SDK runner, read-before-edit policy, approved tool set, one-mutation boundary, turn/timeout bounds, and the fact that the patcher cannot run the failed test.

- [x] **Step 2: Run non-network regression verification**

Run:

```bash
pytest -q tests/test_pi_patcher.py tests/test_direct_patcher.py tests/test_patch_only.py tests/test_patch_confirmation.py tests/test_analyze_rq1.py
```

Expected: all tests pass.

- [x] **Step 3: Run a synthetic Yunwu repair**

Invoke one isolated guided patch with `deepseek-v3.2`. Accept only a saved valid `SKILL.md` change produced after both required reads, with no execution tools used.

- [x] **Step 4: Run one genuine saved-failure chain**

Use a fresh output identity to execute patch generation against the saved SkillRACE failure, then independently launch the exact replay and RQ1 analysis. Record the actual terminal status, tokens, credits, wall time, and whether replay changed failure to pass; do not repeat an outcome-unknown semantic patch.

**July 14 result.** The deterministic guided patch completed after five model turns and
one successful `SKILL.md` mutation (17,882 uncached input, 11,008 cache-read, 2,888
output tokens, 86.5 seconds, 0.066444 Yunwu credits). The independent exact replay ran
once and returned `same_failure` (49,160 input, 515,712 cache-read, 16,421 output,
1.179007 credits), so the case correctly contributes zero confirmed defects. Inspection
showed that the saved failure itself is not a valid positive repair gate: one checker
requires JSON output absent from the task, while another ignores the real
`parse_sensor_data` function and invokes `main` with an incompatible argument. The next
pre-headline gate is therefore semantic checker compilation/audit, not another replay of
this patch. Recursive bounded-development RQ1 verification accepted the campaign,
unchanged-skill confirmations, patch ledger and exact-replay ledger and emitted a verified
cell with two raw failure observations, one reproduced cluster and zero confirmed events.
