# Configurable Patch-Only Skill Repair Implementation Plan

> **Superseded for the Pi backend:** the guided SDK read→edit workflow in
> `2026-07-14-guided-pi-repair.md` replaces this plan's original CLI tool list.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add frozen per-method `direct`/`pi` patch backends that produce one isolated skill patch per public failure without executing or replaying it.

**Architecture:** Extend the campaign protocol with a validated repair policy, enrich the existing method-assisted evidence contract, and introduce a patch-only state machine separate from historical combined repair/replay. Route direct patches through the journaled model client and SkillRACE Pi patches through a confined read/edit-only container. RQ1 and RQ3 drivers publish patch-only ledgers; later confirmation consumes immutable completed patch receipts.

**Tech Stack:** Python 3.12, pytest, Docker, Pi 0.73.1, Yunwu OpenAI-compatible APIs, canonical JSON receipts.

---

### Task 1: Frozen configurable repair policy

**Files:**
- Modify: `skillrace/campaign_protocol.py`
- Modify: `tests/test_campaign_protocol.py`
- Modify: `tests/test_protocol_authority.py`
- Modify: `experiments/protocols/issta-main*.json`
- Modify: `experiments/protocols/pilot*.json`

- [ ] Add failing tests that require a `repair` object with `enabled`, `timeout_seconds`, `max_output_tokens`, `temperature`, `reasoning`, and an exact `backend_by_method` mapping over `random`, `greybox`, and `skillrace`.
- [ ] Verify rejection of unknown backends, missing methods, per-case overrides, timeout outside `1..600`, and output limits outside `1..65536`.
- [ ] Add immutable `RepairPolicy` parsing and `backend_for(method)`; use the intended mapping `skillrace=pi`, `greybox=direct`, `random=direct` in active draft and pilot protocols.
- [ ] Run `python -m pytest -q tests/test_campaign_protocol.py tests/test_protocol_authority.py` and confirm green.

### Task 2: Exact common evidence and maximum SkillRACE evidence

**Files:**
- Modify: `skillrace/repair_validation.py`
- Modify: `tests/test_repair_validation.py`
- Modify: `docs/data-contracts.md`

- [x] Add failing tests proving the common payload retains the exact task prompt, environment description, relevant input-file identities, failed artifact representation, checker errors and executable conditions without a dependency-version field.
- [x] Add failing tests proving baseline payloads contain no reasoning/tree/guard/tool evidence while SkillRACE payloads preserve ordered reasoning blocks, tool calls/results, tree path, guard mutation, intended/observed branch and targeting classification.
- [ ] Replace lossy 320-character task/environment clipping with bounded structured fields and deterministic failure-adjacent evidence retention. Preserve the common core before truncating SkillRACE-only evidence.
- [ ] Stage read-only `common-evidence.json` and `skillrace-evidence.json` artifacts with canonical hashes for the Pi backend; direct backends consume only the common canonical payload.
- [ ] Run `python -m pytest -q tests/test_repair_validation.py` and confirm green.

### Task 3: Patch-only exactly-once state machine

**Files:**
- Create: `skillrace/patch_only.py`
- Create: `tests/test_patch_only.py`
- Modify: `skillrace/repair_validation.py`

- [ ] Write failing tests for one patch intent, terminal resumption, timeout without retry, crash-to-`outcome_unknown`, completed patched-skill receipt, and the absence of replay/checker artifacts.
- [ ] Write failing tests rejecting empty `SKILL.md` changes, escaped paths, invalid packages, added/removed files and changes to any file other than `SKILL.md`.
- [ ] Implement `patch_failed_execution(request, evidence, *, backend)` and `patch_campaign_failures(...)` with statuses `completed`, `timeout`, `error`, `invalid_patch`, and `outcome_unknown`.
- [ ] Store only patched package, unified `SKILL.md` diff, hashes, backend/model/config, timing, token/cost accounting and crash-safe receipts. Do not store raw responses, rationales or replay artifacts.
- [ ] Keep historical `repair_failed_execution` and `repair_campaign_failures` readable for old artifacts but mark new drivers as patch-only.
- [ ] Run `python -m pytest -q tests/test_patch_only.py tests/test_repair_exactly_once.py` and confirm green.

### Task 4: Direct single-call backend

**Files:**
- Create: `skillrace/direct_patcher.py`
- Create: `tests/test_direct_patcher.py`
- Modify: `skillrace/revise_skill.py`

- [ ] Add a failing test showing one journaled call receives the original `SKILL.md` plus common evidence, never SkillRACE-only evidence, and requests only a complete replacement `SKILL.md`.
- [ ] Add failing tests that the system/user prompts explicitly forbid execution, tests, checker invocation, replay, artifact repair and iterative validation.
- [ ] Implement `make_direct_patcher(policy, model, chat_fn=chat)` using one semantic operation identity and the shared model journal. Normalize the response in memory and do not persist raw response text.
- [ ] Copy the original package, replace only `SKILL.md`, validate structural invariants and return minimal usage/cost metadata.
- [ ] Run `python -m pytest -q tests/test_direct_patcher.py` and confirm green.

### Task 5: Constrained Pi patch backend

**Files:**
- Create: `skillrace/pi_patcher.py`
- Create: `tests/test_pi_patcher.py`
- Modify: `images/pi-base/README.md`

- [ ] Add failing command-construction tests requiring `--tools read,grep,find,ls,edit,write`, `--no-skills`, `--no-extensions`, `--no-prompt-templates`, `--no-context-files`, the frozen model, and the absence of the `bash` tool.
- [ ] Add failing mount tests requiring one writable isolated skill, read-only common and SkillRACE evidence, no checker/confirmation mount, and no hidden-oracle path.
- [ ] Add failing tests for a 300-second protocol timeout, forced container cleanup, no retry, ephemeral session deletion, and minimal cost extraction.
- [ ] Implement `make_pi_patcher(policy, model, image)` using argv-based `docker run`, a unique container name, provider-key environment forwarding, provider-only egress, and a patch-only system prompt.
- [ ] After Pi exits, reject all package changes except a nonempty `SKILL.md` edit; extract usage/cost, delete the session, and return only the patched package and minimal metadata.
- [ ] Run `python -m pytest -q tests/test_pi_patcher.py` and confirm green.

### Task 6: Backend routing in RQ1 and RQ3

**Files:**
- Modify: `skillrace/experiment_driver.py`
- Modify: `skillrace/rq3_pipeline.py`
- Modify: `skillrace/rq3_driver.py`
- Modify: `tests/test_experiment_driver.py`
- Modify: `tests/test_rq3_pipeline.py`
- Modify: `tests/test_rq3_phase_isolation.py`

- [ ] Add failing driver tests proving frozen routing resolves SkillRACE to Pi and Random/Greybox to direct, with no runtime override.
- [ ] Add failing tests proving the patch phase stops after the patched receipt and never invokes the existing replay executor or confirmation executor.
- [ ] Replace new-schedule calls to `repair_campaign_failures` with `patch_campaign_failures`; retain historical ledger readers.
- [ ] Route resource slots correctly: direct uses the API slot; Pi uses API, Docker and agent slots. Forward the shared model and frozen 300-second patch timeout.
- [ ] Write patch-policy launch receipts for RQ1 and RQ3 and validate their backend/model/evidence hashes recursively.
- [ ] Run the focused RQ1/RQ3 driver and isolation tests and confirm green.

### Task 7: Later confirmation boundary

**Files:**
- Modify: `skillrace/rq3_confirmation.py`
- Modify: `tests/test_rq3_confirmation.py`
- Modify: `skillrace/analyze_rq1.py`
- Modify: `skillrace/development_gate.py`

- [ ] Add failing tests that confirmation accepts only a completed immutable patch receipt and cannot call either patch backend.
- [ ] Add a patch-receipt-to-confirmation request adapter without launching confirmation during patch generation.
- [ ] Update analysis terminology to distinguish `failed`, `patched`, and later `repair_confirmed`; do not count a patch as a defect before successful confirmation.
- [ ] Run the confirmation, analysis and development-gate tests and confirm green.

### Task 8: Documentation and full verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-11-skillrace-evaluation-design.md`
- Modify: `docs/pipeline-walkthrough.md`
- Modify: `docs/evaluation-reviewer-guide.md`
- Modify: `docs/implementation-status.md`
- Modify: `STATUS.md`

- [ ] Synchronize the evaluation spec with configurable patch-only backends, the intended asymmetric mapping, the no-execution restriction, and later independent confirmation.
- [ ] Document that patches output no rationale or retained Pi trace and receive no separate dependency-version report.
- [ ] Run focused repair tests, then `python -m pytest -q`.
- [ ] Run `python -m compileall -q skillrace tests`, `bash -n` on changed shell files, JSON parsing for changed manifests, and `git diff --check`.
- [ ] Run one bounded development smoke with a fake/non-production model transport proving that Pi can edit `SKILL.md` without `bash` or replay; do not use headline results.
- [ ] Confirm `docker ps` and project-process inspection show no repair containers or background launchers.
