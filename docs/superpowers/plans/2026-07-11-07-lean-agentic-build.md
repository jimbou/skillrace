# Lean SkillRACE Agentic Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and validate the reproducible 30-run SkillRACE system with exactly random and VeriGrey-inspired baselines, plus a lean four-condition skill-generation experiment.

**Architecture:** Pure protocol/accounting modules own fairness and resumption; generators own only method-specific proposal/fold state; immutable workers execute shared sanity, runner, and oracle stages; one reducer owns adaptive state. Offline and Docker gates precede qwen3.6-flash pilots, and all prompts remain skill-agnostic.

**Tech Stack:** Python 3.12, pytest, Docker, Pi, CloseAI qwen3.6-flash, JSON/JSONL, Bash.

---

## Fixed experiment contract

- Random: 30 fresh independent tests, no initialization corpus or execution feedback.
- VeriGrey-inspired: 10 bootstrap executions plus 20 tool-sequence-guided mutations.
- SkillRACE: 10 bootstrap executions plus 20 reasoning/property-guided tests.
- All bootstrap executions count and can discover defects.
- One shared qwen3.6-flash configuration, runner, sanity gate, and property checker.
- RQ3 feedback producers are the same three methods with the same 30-run allocation.
- RQ3 hidden evaluation compares zero-shot, random-feedback, VeriGrey-feedback, and SkillRACE-feedback once on each hidden test.
- No additional full baseline. Only an optional five-skill outcomes-only SkillRACE ablation.

### Task 1: Establish the offline reliability foundation

**Files:**
- Create: `pyproject.toml`, `skillrace/io_utils.py`
- Modify: `.gitignore`, `skillrace/run_case.py`, `skillrace/compile_checks.py`
- Test: `tests/test_io_utils.py`, `tests/test_runner_status.py`, `tests/test_compile_identity.py`

- [ ] Write tests first for canonical hashes, crash-safe atomic JSON replacement, Pi-status preservation through cleanup, and cache invalidation when properties, prompt, environment, prompt version, image digest, or model changes.
- [ ] Run each new test and observe the expected missing-behavior failure.
- [ ] Implement the minimum helpers and route runner/check manifests through them.
- [ ] Preserve run artifacts on agent error; raise Pi's real return code after artifacts exist.
- [ ] Remove compiler-owned temporary images in `finally` blocks.
- [ ] Run the focused tests and complete offline suite, then commit.

### Task 2: Fix applicability, accounting, and resumable records

**Files:**
- Create: `skillrace/property_specs.py`
- Modify: `skills/*/applicability.json`, `skillrace/fixed_checks.py`, `skillrace/check_properties.py`, `skillrace/aggregate.py`, `skillrace/tree.py`, `skillrace/guards.py`
- Test: `tests/test_property_specs.py`, `tests/test_campaign_outcomes.py`, `tests/test_aggregate_metrics.py`, `tests/test_atomic_call_sites.py`

- [ ] Write failing tests proving all 28 applicability matrices reference real property IDs, pre-agent infrastructure failures do not consume budget, started errors/timeouts do consume budget, first-defect indexes are one-based, and no-defect campaigns are right-censored.
- [ ] Implement explicit property selection and fixed-invariant allowlists.
- [ ] Replace mutable shared JSON writes with atomic replacement.
- [ ] Record infrastructure, agent, oracle-inconclusive, and generation outcomes separately.
- [ ] Run focused and full tests, then commit.

### Task 3: Implement the fair 30-run generator protocol

**Files:**
- Create: `skillrace/campaign_protocol.py`, `skillrace/sanity.py`
- Modify: `skillrace/generator.py`, `skillrace/greybox.py`, `skillrace/loop.py`, `scripts/run_suite.sh`, `README.md`
- Test: `tests/test_campaign_protocol.py`, `tests/test_candidate_sanity.py`, `tests/test_greybox_initialization.py`, `tests/test_baseline_information_boundaries.py`

- [ ] Write failing tests for the exact `30/0+30`, `30/10+20`, and `30/10+20` allocations and rejection of role-specific model overrides.
- [ ] Prove random never enters bootstrap code and cannot read traces/properties/tree state.
- [ ] Prove VeriGrey retains all ten initial seeds, populates coverage from all of them, and applies novelty filtering only to later mutants.
- [ ] Add one shared candidate schema/build/path/tool/invocation/unsolved/syntax sanity gate; rejected candidates never call Pi or consume budget.
- [ ] Keep the realization/build/repair implementation byte-identical across methods.
- [ ] Change user-facing/default experiment commands to budget 30 and seed count 10.
- [ ] Run focused and full tests, then commit.

### Task 4: Build the exactly-once campaign engine and cleanup lifecycle

**Files:**
- Create: `skillrace/campaign_engine.py`
- Modify: `skillrace/loop.py`, `skillrace/generator.py`, `skillrace/greybox.py`
- Test: `tests/test_campaign_engine.py`, `tests/test_generator_snapshots.py`, `tests/test_image_lifecycle.py`

- [ ] Write fake-executor tests for equal counted budgets, attempt caps, interruption after committed execution two, exact resume through execution thirty, and protocol-hash mismatch refusal.
- [ ] Implement deterministic execution/attempt IDs, atomic manifests, generator snapshots, and receipt recovery.
- [ ] Regenerate after pre-agent generation failure without advancing the 30-run counter.
- [ ] Clean candidate images exactly once after their final compiler/runner consumer.
- [ ] Run focused and full tests, then commit.

### Task 5: Add bounded parallel epochs without changing search semantics

**Files:**
- Create: `skillrace/resource_pool.py`, `skillrace/parallel_campaign.py`
- Modify: `skillrace/campaign_engine.py`, `skillrace/guards.py`, `skillrace/loop.py`
- Test: `tests/test_resource_pool.py`, `tests/test_parallel_campaign.py`, `tests/test_skillrace_classification.py`, `tests/integration/test_epoch_replay.py`

- [ ] Write failing tests for global API/Docker/agent limits, immutable worker output, deterministic candidate-ID fold order, and replay equality across completion orders.
- [ ] Implement random/greybox queues and SkillRACE frozen-tree epochs with one reducer.
- [ ] Record intended-branch, different-new-branch, no-divergence, path-miss, targeted, and serendipitous outcomes without requiring branch success.
- [ ] Keep synthesis opportunistic: coherent multi-feature mutations are allowed and all valid discoveries fold back into the tree.
- [ ] Add an outcomes-only strategy boundary but do not schedule other ablations.
- [ ] Run focused and full tests, then commit.

### Task 6: Harden the ten-scenario hidden benchmark economically

**Files:**
- Create: `skillrace/scenario_contract.py`, `skillrace/scenario_audit.py`
- Modify: `scenarios/text-template/tests/*/candidate.json`, `scenarios/json-csv/tests/t5/checks/no-crash.sh`, error/performance checks, `scenarios/fix-failing-test/tests/*/checks/tests-unedited.sh`
- Test: `tests/test_scenario_contract.py`, `tests/test_scenario_oracles.py`

- [ ] Write structure/hash tests for exactly ten scenarios and ten hidden tests each.
- [ ] Correct double-brace prompts, require real empty-CSV behavior, and make error/performance checks prove artifact existence, command execution, return code, and result.
- [ ] Strengthen test-integrity checks against edit, deletion, rename, skip, assertion weakening, and harness override.
- [ ] Store one reference overlay and at least one negative implementation per hidden test; add targeted mutants only for the identified weak checks.
- [ ] Run syntax/static gates, then Docker reference/negative audits with no model or agent calls.
- [ ] Commit validation evidence and code.

### Task 7: Implement lean RQ3 feedback, revision, and hidden evaluation

**Files:**
- Create: `skillrace/feedback.py`, `skillrace/rq3.py`, `skillrace/analyze_rq3.py`
- Modify: `skillrace/revise_skill.py`, `skillrace/skill_eval.py`
- Test: `tests/test_feedback.py`, `tests/test_rq3_leakage.py`, `tests/test_rq3_manifest.py`, `tests/test_skill_eval_metrics.py`, `tests/test_analyze_rq3.py`

- [ ] Write failing tests that physically exclude `tests/` from campaign/revision stages and detect sentinel leakage.
- [ ] Normalize all methods into the same ordered, bounded feedback schema; separate confirmed and inconclusive findings.
- [ ] Make revision requests identical except for envelope content and record every prompt/model/hash/cost input.
- [ ] Evaluate exactly four conditions once per hidden test using byte-identical cases/checks.
- [ ] Report paired hidden-test pass-rate changes from zero-shot, strict pass rate, cost, and per-scenario effects.
- [ ] Link base, campaign, envelope, revision, hidden-test, run, and result hashes in one resumable manifest.
- [ ] Run focused and full tests, then commit.

### Task 8: Validate with diverse real pilots and produce the artifact report

**Files:**
- Create: `skillrace/defect_triage.py`, `skillrace/analyze_rq1.py`, `skillrace/artifact.py`, `scripts/artifact_smoke.sh`
- Modify: `docs/implementation-status.md`, `README.md`, `paper/skillrace.tex`
- Test: `tests/test_defect_triage.py`, `tests/test_analyze_rq1.py`, `tests/test_artifact.py`

- [ ] Run the complete offline suite and sub-30-minute replay smoke from a clean checkout.
- [ ] Run Docker smoke campaigns for random, VeriGrey-inspired, and SkillRACE on debugging, CLI, parser, SQL, and low-contingency skills.
- [ ] With explicit API authorization, run small qwen3.6-flash pilots, inspect traces/trees/guards/checks, and fix only general cross-skill failures with regression tests.
- [ ] Never change a baseline or prompt merely because a pilot result is unfavorable; fairness tests and frozen protocol remain authoritative.
- [ ] Deduplicate suspected defects by failure signature and confirm one representative once.
- [ ] Generate per-skill/family yield, discovery, censoring, branch, rejection, timeout, token, cost, and wall-time outputs.
- [ ] Run a lean one-scenario RQ3 end-to-end pilot before scaling.
- [ ] Document final architecture, decisions, commands, limitations, and artifact reproduction in plain language, then request final code review and verify every claim.

## Completion gate

The build is complete only when the clean offline suite passes, Docker oracle audits pass,
the three methods demonstrate exact 30-run accounting and information isolation, replayed
parallel epochs are deterministic, RQ3 leakage tests pass, real multi-family pilots leave
complete artifacts, and the human-readable report can reproduce every claimed result.
