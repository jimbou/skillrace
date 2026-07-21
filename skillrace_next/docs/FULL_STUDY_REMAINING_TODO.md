# Full-Study Remaining TODO

Date: 2026-07-21

## Purpose

Tasks 1–16 and the earlier bounded contracts remain valid evidence for the clean-room
runtime, verifier, Docker executor, patcher, replay, and sequential campaign composition.
Do not start the full experiment with the current development-test selection semantics,
however. The scientific rules below supersede the pilot's generated-check and one-seed
initialization behavior and must be implemented and live-verified first.

Work remains restricted to `skillrace_next/` and `tests_next/`. Do not import or modify
the legacy `skillrace` package, retry unfavorable scientific outcomes, or perform the
legacy cutover.

## Frozen full-study shape

- Part I: the selected 30 existing S0 skills.
- Part II: the selected ten public scenarios and their 100 already-frozen held-out tests.
- Methods: Random, VeriGrey, and SkillRACE.
- Model tracks: `lab/deepseek-v4-flash` and `lab/qwen3.6-flash`.
- Non-verifier roles: the same cheap model for every role within one track.
- Verifier: real Codex `gpt-5.6-terra` with medium reasoning, read-only inputs, and no
  Docker access.
- Development budget: 30 weak-agent test executions per method and skill/scenario.
- Replicates: one.
- Held-out repetitions: one.
- Execution: direct sequential loops only.

## Settled scientific behavior

### Frozen NL checks are authoritative

Before any full campaign starts, prepare and freeze:

- one development NL-check catalog for each of the 30 Part I skills; and
- one development NL-check catalog for each of the ten Part II public scenarios.

Part II development catalogs must be written from the public scenario before generating
S0. They must not be derived from, or reveal, the held-out tests. The held-out records
remain separate and are loaded only after all methods finish development.

Every generated development test receives the complete catalog for its skill/scenario.
No method selects a subset, returns check IDs, authors a new check description, weakens a
check, or changes the catalog order. The exact same catalog hash is attached to every
Random, VeriGrey, and SkillRACE development test in that campaign.

Terra must account for every supplied property by either:

- authoring an executable checker for it; or
- recording it as `uncovered` with a reason when it is not observable in that particular
  task/artifact.

`uncovered` is ignored for pass/fail and patch admission. It is never silently omitted,
treated as passing, or used to trigger a patch. Only authoritative Docker `fail` results
can trigger patching. Patch admission remains: at least one former failure becomes a pass,
and no former pass becomes a failure. Retained previously passing tests must also remain
passing.

### Generated tests contain a prompt and a real Docker environment

Each test-generation call produces the visible task prompt and its Docker environment.
Use the smallest direct JSON/file contract needed to materialize a Dockerfile and bounded
build context. The generated task must be feasible under its own environment and
permissions. Validation builds the environment, pins the resulting image ID, and rejects
an impossible or malformed test before weak-agent execution.

An unusual or incomplete environment is allowed and is part of the test. If the task is
feasible but the agent fails to inspect or repair the environment, that is a real task
failure, not automatically `inconclusive`. Examples include locating a binary, adjusting
`PATH`, installing a dependency, or creating a system symlink when the container permits
it. A requirement that cannot be satisfied even by the configured root weak agent is an
invalid test. Provider, Docker-daemon, or corrupted-evidence failures remain
infrastructure outcomes.

### Weak agents run as container root

Run the weak task agent as root inside its owned task container. Do not add
`--privileged`, mount the Docker socket, give the agent Docker access, make the installed
skill writable, or broaden host mounts. Terra remains outside Docker and read-only.

Before removing the task container on completion, timeout, or failure, normalize the
artifact and runtime-evidence ownership to the host UID/GID. Preserve partial artifacts
and traces on timeout. Checker execution and cleanup remain authoritative and
exception-safe.

### Higher sampling applies only to test generation

Use an explicit test-generation temperature of `1.0` for both model tracks. Record it in
each Pi proposal receipt. Apply it only to:

- Random test proposals;
- VeriGrey seed materialization and mutations; and
- SkillRACE's initial diversity plan, initial test materialization, and later
  branch-directed test proposals.

Do not raise the temperature for weak-agent execution, base-skill generation, episode
creation, tree merging/alignment, skill generation, patching, replay, or Terra. Add the
smallest explicit Pi sampling input needed; do not create a general model-settings
framework.

### Random is independent black-box generation

For each of 30 iterations, Random independently generates a fresh prompt and Docker
environment from the current skill/scenario and the complete frozen NL-check catalog.
Random receives no previous tests, traces, coverage, results, or accumulated method state.
Some duplicate behavior is a legitimate baseline outcome; the explicit temperature and
full catalog encourage diversity without adding adaptive feedback.

### SkillRACE uses ten frozen diverse seeds, then the tree

The 30-execution SkillRACE budget has two direct phases:

1. Before executing any SkillRACE development test, one planning call reads the current
   skill/scenario and the complete frozen NL-check catalog and returns exactly ten diverse
   high-level test descriptions. Each description contains only the intended task and
   important Docker-environment conditions. It does not select or emit NL-check IDs. The
   ordered ten-description plan is hash-bound before its first execution.
2. Materialize the ten descriptions one at a time into a prompt and Docker environment.
   Every materialized test receives the entire frozen NL-check catalog. Execute each test,
   create episodes, and merge/update the reasoning tree, but select the next test only from
   the frozen initial list.
3. After all ten initial tests have executed, use ordinary SkillRACE unreached-branch
   selection for executions 11–30. Branch-directed proposals also see and carry the entire
   frozen NL-check catalog.

The one planning call is proposal overhead and does not consume one of the 30 weak-agent
executions. Part II patching and accepted-skill carry-forward continue normally during
both phases.

### VeriGrey initializes a seed corpus before mutation

Follow the explicit process in `verigrey.pdf` rather than starting mutation after one
usable test:

1. Construct one initial seed description for each frozen NL check. A check is the seed's
   generation focus only; every resulting test is still evaluated against the complete
   catalog.
2. Freeze and materialize the complete initial seed corpus before using execution
   feedback.
3. Execute every seed and record its normalized tool sequence.
4. Only after seed initialization, choose corpus seeds and spend the remaining executions
   on LLM mutations informed by tool, transition, and full-sequence novelty.
5. Add offspring with new coverage to the seed corpus and preserve all selection, energy,
   mutation, and observation evidence.
6. Stop when the total number of seed plus mutation executions reaches 30.

The paper used one seed per injection objective: an average of 8.7 initial seeds within a
100-execution campaign, or about 8.7%. The prepared Part I catalogs currently contain
2–8 properties and average 3.3, so one seed per property averages 11% of this study's
30-execution budget. The number of Part II development properties should be kept focused;
three gives a comparable 10% initialization share.

The paper does not specify the exact `ChooseSeed` tie-breaking algorithm. Before coding
that detail, freeze one small deterministic rule consistent with its seed queue and
novelty/energy definitions. Do not silently retain the current least-transition proposer,
which does not maintain or mutate the paper's seed corpus.

### One fixed task-execution timeout

Use valid pilot evidence to measure weak-agent wall-time distributions. Exclude provider,
Docker, invalid-test, and interrupted infrastructure cases. Choose and document one fixed
cutoff before the revised bounded live gate and use it unchanged for:

- Part I and Part II;
- S0, Random, VeriGrey, and SkillRACE;
- DeepSeek and Qwen; and
- original development execution and post-patch task replay.

Proposal, patch-authoring, Docker-build, checker, and Terra timeouts remain separate role
timeouts. Do not choose or revise the weak-execution cutoff after inspecting whether a
particular S0 fails or a patch passes. An agent timeout is a real outcome: preserve and
check its partial artifact without retrying for luck.

## Ordered implementation TODO

Use TDD and a small focused commit for every item. For each behavior: write the failing
test, confirm the expected failure, implement the minimum direct behavior, run focused
offline tests, run its separate paid live contract when required, inspect and retain
sanitized evidence, and commit only that item.

- [ ] Audit and finalize every Part I development NL-check catalog. Author and freeze the
  ten Part II development catalogs from public scenarios only. Add ordered hashes and
  provenance receipts, then update affected Part I receipts/manifests.
- [ ] Change the generated-test contract so proposers generate a prompt and bounded Docker
  environment while the pipeline attaches the complete frozen NL-check catalog. Reject
  generated check prose, selected check lists, unknown catalog hashes, infeasible builds,
  and hidden prompt/check contradictions.
- [ ] Make Terra account for every supplied property as executable or explicitly
  `uncovered`. Confirm that uncovered properties neither pass nor fail and cannot trigger
  patching.
- [ ] Run weak task agents as container root, preserve all existing isolation boundaries,
  and restore host ownership on every terminal path.
- [ ] Add one explicit `temperature=1.0` Pi input for test-generation calls and record it in
  receipts. Leave every non-generation call unchanged.
- [ ] Update Random to produce 30 independent prompt/environment proposals carrying the
  full frozen catalog and no accumulated state.
- [ ] Replace VeriGrey's one-seed shortcut with the frozen initial corpus, full seed
  execution, corpus state, novelty feedback, bounded energy, mutation, and total-execution
  accounting described above. Freeze the paper's under-specified seed-selection tie-break
  before implementation.
- [ ] Add SkillRACE's one-call ten-description diversity plan, freeze it, materialize and
  execute those descriptions in order, update the tree after each, and switch to normal
  branch-directed selection only for executions 11–30.
- [ ] Recheck Part I immutable-S0 behavior and Part II accepted-skill carry-forward under
  all three revised method loops. Patching remains same-track Pi work; Terra only checks.
- [ ] Extract valid pilot timing data, publish the distribution and exclusions, select the
  fixed weak-execution cutoff, and bind it into the revised experiment configs.
- [ ] Run the full offline suite and the separate live contracts listed below. Manually
  inspect semantic outputs, scan exact active credentials, and confirm container cleanup.
- [ ] Freeze 80 full-study campaign configs: 30 Part I plus ten Part II campaigns for each
  of two model tracks. Use unique experiment IDs and output roots, 30 iterations, one
  replicate, and one held-out repetition.
- [ ] Verify every frozen input and config hash immediately before launch. Run all campaigns
  sequentially without retrying unfavorable scientific outcomes. Stop on persistent
  provider failure.
- [ ] Verify all expected terminal campaign outputs, scan evidence for credentials, and
  confirm that no owned Docker container remains.
- [ ] Add one small direct aggregation script after the final output layout exists. Report
  S0 and method outcomes, accepted/rejected revisions, regressions, timeouts, invalid and
  uncovered tests, costs, and the already-defined Part I/Part II metrics.

## Required revised live contracts

Require `--live` and store sanitized evidence under
`out/live-contracts/<component>/<run-id>/`. A later end-to-end run does not replace an
individual component contract.

- [ ] Real DeepSeek and Qwen test-generation calls prove explicit temperature provenance,
  prompt/environment generation, and exact full-catalog attachment.
- [ ] A real root weak-agent task repairs an achievable system-environment condition,
  preserves its artifact/trace, restores host ownership, and receives authoritative
  Docker results.
- [ ] Real Terra receives the full catalog, authors checks for applicable properties,
  explicitly records inapplicable ones as uncovered, never invokes Docker, and is followed
  by real authoritative `docker exec` results.
- [ ] Real Random runs independently twice per model without receiving prior state. Do not
  require the stochastic outputs to differ as a pass condition.
- [ ] Real VeriGrey executes its full seed corpus before its first feedback-guided mutation,
  then preserves seed choice, energy, mutation, tool-sequence, and corpus-admission evidence
  for both models.
- [ ] Real SkillRACE produces and freezes ten semantically diverse descriptions, executes
  them in order while building the tree, and uses a tree-selected branch only afterward.
  Inspect the plan, first materialized seed, tenth tree update, and first branch-directed
  proposal for both models.
- [ ] Real same-track DeepSeek and Qwen patch/replay contracts prove that an achievable
  environment failure can produce guidance, exact replay uses the frozen Terra bundle,
  and admission still requires improvement without regression.
- [ ] A real bounded timeout contract preserves and checks a partial artifact without
  retrying the weak agent.
- [ ] After all individual contracts pass, run one fresh bounded revised pipeline gate for
  each model track before freezing the 80 full-study configs.

## Manual inspection and evidence rules

Manually inspect the first revised Random test, VeriGrey seed corpus and mutation,
SkillRACE ten-test plan and post-seed branch proposal, root environment repair, generated
skill, patch, Terra bundle, Docker JSON result, and exact replay. Valid JSON is not enough.
Preserve image IDs, config/catalog hashes, provider/model/temperature provenance, costs,
timeouts, cleanup receipts, and sanitized traces. Never give Terra Docker access or use
Yunwu/Lab in place of Terra for checker authoring.
