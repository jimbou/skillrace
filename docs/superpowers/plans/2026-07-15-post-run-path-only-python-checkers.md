# Post-run Path-Only Python Checkers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace pre-run generated Bash plus semantic self-audit with method-blind post-run Python checker generation from task metadata and final workspace paths only.

**Architecture:** `RealCampaignExecutor` runs sanity and the agent before invoking the shared checker. `check_properties.py` snapshots the finished container, collects path/tool metadata, calls a reduced `compile_checks.py` Python author/compiler once per property with one syntax retry, then executes each frozen checker in a fresh child. Fixed checks and human-authored hidden checks remain unchanged.

**Tech Stack:** Python 3 standard library, existing Docker isolation, durable Yunwu journal/receipts, pytest.

---

### Task 1: Define the path-only Python authoring contract

**Files:**
- Modify: `tests/test_checker_semantic_audit.py`
- Modify: `skillrace/compile_checks.py`

- [ ] Write a failing test for `author_python_check(...)`. Capture `chat` and assert the prompt contains the task, environment description, property, tools, and final paths, but none of sentinel file contents, diff, trace, method, verdict, or prior result. Assert no output-token limit and a 120-second timeout.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_checker_semantic_audit.py -k path_only_prompt` and confirm it fails because the API is absent.
- [ ] Replace the active Bash/audit prompt with a versioned Python prompt defining exits `0/1/2`, required-artifact absence, conditional preconditions, and forbidden network/install/Docker behavior. Implement `author_python_check(...)` and `validate_python_source(...)` using `compile(source, filename, "exec")`.
- [ ] Remove semantic-audit calls, semantic rewrites, Bash policy regexes, and change-diff enforcement from the active generated-checker path.
- [ ] Re-run the focused test and confirm it passes.

### Task 2: Compile post-run checkers with one retry and exclusions

**Files:**
- Modify: `tests/test_checker_semantic_audit.py`
- Modify: `tests/test_compile_identity.py`
- Modify: `skillrace/compile_checks.py`

- [ ] Write failing tests for `compile_post_run_checks(...)`: valid Python becomes `<property-id>.py`; syntax failure retries once with the error and previous source; a second failure excludes only that property; ordinary author failure excludes only that property; `OutcomeUnknownError` propagates; all-excluded produces a valid zero-active manifest; no semantic-audit field or call exists.
- [ ] Add failing identity tests binding final snapshot identity, path-tree hash, prompt/policy version, scripts, exclusions, author receipts, tokens, cache reads, costs, and unknown-cost status. Identical inputs reuse cache; tree/snapshot/policy drift invalidates it.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_checker_semantic_audit.py tests/test_compile_identity.py` and verify the expected failures.
- [ ] Implement `compile_post_run_checks(...)` writing `<run>/checks/manifest.json`, authoring every property independently, retrying syntax once, continuing after exclusions, and returning `(manifest, known_cost)`.
- [ ] Re-run the Task 2 tests and confirm they pass.

### Task 3: Execute Python checkers with three-state verdicts

**Files:**
- Modify: `tests/test_check_isolation.py`
- Modify: `skillrace/check_properties.py`

- [ ] Write failing real-script tests mapping exit `0` to holds, `1` to violated, and `2` to not considered. Add timeout, signal/unexpected exit, staging failure, and unavailable-Python cases, all not considered. Retain the fresh-child contamination test using Python.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_check_isolation.py` and verify failures because the runner still invokes Bash and treats all nonzero exits as violations.
- [ ] Stage `.py` bytes and invoke `python3` in each networkless capability-dropped child. Preserve timeout cleanup. Emit `holds: null`, `violated: false`, and `not_considered: true` for checker/infrastructure failures.
- [ ] In `check_properties.main`, snapshot first, collect final relative paths/tools, load the original properties, call `compile_post_run_checks`, execute active entries, append exclusions as not considered, and always run fixed checks.
- [ ] Remove active precompiled/post-hoc generated Bash branches without changing human-authored RQ3 hidden checks.
- [ ] Re-run checker isolation and focused compiler tests.

### Task 4: Move checker generation after the agent

**Files:**
- Modify: `tests/test_campaign_engine.py`
- Modify: `tests/test_campaign_outcomes.py`
- Modify: `tests/test_generation_attempt_ownership.py`
- Modify: `skillrace/loop.py`

- [ ] Write failing executor tests asserting `materialize → runtime integrity → sanity → agent → snapshot/author → checks`. Assert authoring gets prompt, environment provenance, properties, applicability, and model but no method/source label. Failed agent launches make no author calls.
- [ ] Add a failing test that post-run checker `OutcomeUnknownError` produces `external-outcome-indeterminate`; ordinary property exclusions remain not considered and never propose another candidate.
- [ ] Run the three test files and verify ordering/outcome failures.
- [ ] Remove `compile_case` from the pre-agent path. Pass blinded candidate metadata and properties to `check_run` only after a terminal agent artifact exists. Record post-run checker cost/accounting in the attempt receipt while keeping counted agent execution semantics unchanged.
- [ ] Re-run the Task 4 tests and confirm they pass.

### Task 5: Reconcile compatibility and documentation

**Files:**
- Modify: `tests/test_rq3_campaign_adapter.py`
- Modify: `tests/test_rq3_pipeline.py`
- Modify: `docs/property-checker.md`
- Modify: `docs/data-contracts.md`
- Modify: `docs/pipeline-walkthrough.md`
- Modify: `STATUS.md`
- Modify: `docs/implementation-status.md`
- Modify: `handoff.md`
- Modify: `docs/2026-07-14-session-handoff.md`

- [ ] Add failing compatibility tests proving every RQ1 method uses the new shared path while RQ3 human-authored hidden checks remain unchanged. Reject old generated-Bash manifests from silent reinterpretation.
- [ ] Add a distinct post-run Python manifest schema and explicit `post-run-path-only` provenance. Update adapters only where required; never relabel old artifacts.
- [ ] Document the blinded path-only limitation, Python exit contract, one retry, exclusions, removed semantic audit, fairness, and receipt fields. Mark earlier Bash/audit pilots as historical diagnostics.
- [ ] Run the focused checker/campaign/RQ3 suite and confirm it passes.

### Task 6: Final offline verification and handoff

**Files:**
- Modify only if verification exposes a scoped defect.

- [ ] Run `.venv/bin/python -m pytest -q -m 'not live'`; expect exit 0 with declared skips only.
- [ ] Run `PYTHON=.venv/bin/python scripts/artifact_smoke.sh`; expect `SkillRACE offline artifact smoke: PASS`.
- [ ] Run `.venv/bin/python -m compileall -q skillrace tests`; expect exit 0.
- [ ] Run `git diff --check`; expect exit 0.
- [ ] Inspect the final diff and update both handoffs with implementation decisions, failures, verification, and remaining live validation. Make no paid call without separate authorization after the offline gate.
