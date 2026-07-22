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

Before any full campaign starts, normalize and freeze the existing source catalogs:

- 28 of the 30 selected Part I skills already provide `skills/<skill>/properties.json`.
  Reuse their ordered `id`/`reads`/`nl` definitions unchanged. `file-check` and
  `js-feature` are the only selected skills without source property files; reuse the
  focused catalogs already prepared for them under `skillrace_next/study/part1/`.
- All ten Part II public scenarios already provide
  `scenarios/<scenario>/campaign/properties.json`. Reuse each ordered three-property
  catalog unchanged; do not author a replacement development catalog.

The normalized clean-room records may rename ordered source IDs to `P1`, `P2`, and so on,
but their descriptions and ordering must remain unchanged and the receipt must preserve
the source ID, `reads` value, source hash, normalized hash, and mapping. Part II catalogs
must be frozen before generating S0. They are not derived from the held-out tests. The
held-out records remain separate and are loaded only after all methods finish development.

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
3. After all ten initial tests have executed, build a compact index of the real observed
   episode-to-episode reasoning edges. A fresh tool-free same-track Pi call selects one
   promising edge ID and explains why it may expose a patchable failure.
4. The host validates that ID and isolates its root-to-edge branch. A second fresh
   tool-free Pi call receives only that branch, the rationale, current skill, and complete
   frozen catalog, then mutates the selected assumption into a prompt and Docker
   environment. It must make the assumption fail without revealing the local recovery
   path or making the task impossible.

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
30-execution budget. Every existing Part II campaign catalog has exactly three properties,
so Part II initialization uses three seeds, or exactly 10% of its budget.

The paper does not specify the exact `ChooseSeed` tie-breaking algorithm. The frozen study
rule is FIFO round-robin: select the oldest queued seed, spend all of its assigned energy,
then append that parent to the queue tail. Append coverage-increasing offspring to the tail
when they are admitted. Energy is `max(1, novelty score)`, capped at three, where the score
awards one point each for new tools, new tool transitions, and a new complete tool sequence.
This keeps the paper's three feedback metrics and ensures a seed with duplicate initial
behavior still receives one mutation. Do not use the former least-transition proposer.

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

- [x] Verify and freeze the existing property catalogs: 28 selected Part I source files,
  the two already-prepared Part I exceptions, and all ten Part II campaign property files.
  Preserve ordered source-to-`P` mappings, `reads` metadata, source and normalized hashes,
  and provenance receipts; update affected Part I receipts/manifests. Do not rewrite
  semantically valid source properties or author duplicate Part II catalogs.
- [x] Change the generated-test contract so proposers generate a prompt and bounded Docker
  environment while the pipeline attaches the complete frozen NL-check catalog. Reject
  generated check prose, selected check lists, unknown catalog hashes, infeasible builds,
  and hidden prompt/check contradictions.
- [x] Make Terra account for every supplied property as executable or explicitly
  `uncovered`. Confirm that uncovered properties neither pass nor fail and cannot trigger
  patching.
- [x] Run weak task agents as container root, preserve all existing isolation boundaries,
  and restore host ownership on every terminal path.
- [x] Add one explicit `temperature=1.0` Pi input for test-generation calls and record it in
  receipts. Leave every non-generation call unchanged.
- [x] Update Random to produce 30 independent prompt/environment proposals carrying the
  full frozen catalog and no accumulated state.
- [x] Replace VeriGrey's one-seed shortcut with the frozen initial corpus, full seed
  execution, corpus state, novelty feedback, bounded energy, mutation, and total-execution
  accounting described above. Freeze the paper's under-specified seed-selection tie-break
  before implementation.
- [x] Add SkillRACE's one-call ten-description diversity plan, freeze it, materialize and
  execute those descriptions in order, update the tree after each, and switch to normal
  branch-directed selection only for executions 11–30.
- [x] Recheck Part I immutable-S0 behavior and Part II accepted-skill carry-forward under
  all three revised method loops. Patching remains same-track Pi work; Terra only checks.
  Focused integration verification covers Random, VeriGrey, and SkillRACE: every Part I
  discovery execution receives the unchanged S0 hash, while Part II carries an accepted
  S0-to-S1 revision into the next iteration and retains S1 after a rejected candidate.
- [x] Extract valid pilot timing data, publish the distribution and exclusions, and select
  the fixed weak-execution cutoff. The 23 valid runs have median 16.548 seconds, p95
  31.620 seconds, and maximum 33.287 seconds. The frozen cutoff is 60 seconds; see
  `study/timing-pilot-v8/TIMING_ANALYSIS.md`.
- [ ] Create one frozen base image per selected Part I skill and Part II scenario before
  freezing the full-study configs. Install only the small tool set appropriate to that
  context, record the immutable image ID and capability context, and give that context to
  test generation. The current Python/Node/Bash/Perl wording applies only to the temporary
  `skillrace-next/task-fixture:test` contract image.
- [ ] Bind the frozen 60-second `timeouts.pi` value into every full-study config. Exact
  replay uses the same value. Non-task Pi calls now use `timeouts.provider`; patch
  authoring, Docker, Terra, and checker limits also remain separate.
- [x] Run the full offline suite after the revised method implementation. The latest
  2026-07-22 verification completed 220 non-live tests with no failures.
- [ ] Complete the separate live contracts listed below. Manually inspect semantic outputs,
  scan exact active credentials, and confirm container cleanup.
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

- [x] Real DeepSeek and Qwen test-generation calls prove explicit temperature provenance,
  prompt/environment generation, and exact full-catalog attachment.
- [x] A real root weak-agent task repairs an achievable system-environment condition,
  preserves its artifact/trace, restores host ownership, and receives authoritative
  Docker results.
  Qwen located `/usr/local/bin/node`, created the required `/usr/bin/node` symlink as
  container root, ran the exact requested command, and passed both authoritative checks.
  The artifact, trace, ownership receipt, and successful cleanup are under
  `out/live-contracts/patcher/qwen3.6-flash/20260721T170024Z-c5a92c29/`.
- [x] Real Terra receives the full catalog, authors checks for applicable properties,
  explicitly records inapplicable ones as uncovered, never invokes Docker, and is followed
  by real authoritative `docker exec` results.
- [x] Real Random runs independently twice per model without receiving prior state. The
  two proposer prompts were byte-identical within each track, while receipts and output
  roots were distinct; output diversity was not a pass condition. Both calls produced
  Docker-valid tests with temperature 1.0. Evidence is under
  `out/live-contracts/test-proposer/deepseek-v4-flash/20260721T164558Z-a519cb54/`
  and `out/live-contracts/test-proposer/qwen3.6-flash/20260721T164700Z-f4c1a73a/`.
- [x] Real VeriGrey executes its full seed corpus before its first feedback-guided mutation,
  then preserves seed choice, energy, mutation, tool-sequence, and corpus-admission evidence
  for both models.
- [ ] Real SkillRACE produces and freezes ten semantically diverse descriptions, executes
  them in order while building the tree, and uses a tree-selected branch only afterward.
  Inspect the plan, first materialized seed, tenth tree update, and first branch-directed
  proposal for both models.
  DeepSeek completed the combined eleven-execution contract: ten ordered seeds built a
  45-node/44-edge tree, then a tree-selected branch produced the eleventh test and a
  50-node/49-edge final tree. Evidence is under
  `out/live-contracts/skillrace-ten-seed/deepseek-v4-flash/20260721T172930Z-31b0bfd6/`.
  Qwen has repeatedly completed real plans, materializations, weak executions, Terra
  bundles, Docker checks, and tree updates, reaching as far as seed nine. Episode creation
  now receives the exact ordered relevant-event IDs and permits two correction calls
  (three total attempts) for both tracks. The exact nine-event Qwen trace that previously
  failed strict coverage/schema validation passed as five semantically correct grounded
  episodes under
  `out/live-contracts/episode-creator/qwen3.6-flash/20260722T002556Z-d4041428/`.
  Trace splitting was deliberately not added because the failure was a schema typo on a
  short trace, not a context-size failure. Initial-test materialization now has the same
  three-total-attempt bound. A fresh Qwen combined run then completed all ten ordered
  weak executions, Terra/Docker checks, episode updates, and tree merges, ending in branch
  phase with 48 nodes and 47 edges. Its first branch mutator response was semantically
  unsuitable and structurally invalid: it used document size as the failure mechanism,
  added an undeclared response field, and wrapped JSON containing embedded fences in an
  outer fence. Preserve
  `out/live-contracts/skillrace-ten-seed/qwen3.6-flash/20260722T002923Z-06536695/`.
  Every SkillRACE model-authored boundary now has the same three-total-attempt correction
  bound. Selector correction does not rerun after a valid edge, mutator correction retains
  that edge, and weak-agent execution remains exactly once. A real Qwen long-tree run used
  one selector call, retained an invalid first mutator attempt, and corrected the mutator on
  its second call. The final Docker-valid task hid the real helper path from the visible
  prompt and was manually inspected under
  `out/live-contracts/skillrace-edge-selector/qwen3.6-flash/20260722T025327Z-87914bff/`.
  Two later combined roots remain diagnostic rather than passing: one exhausted three
  invalid seed-09 materializations after an oversized plan, and one completed nine seeds
  before host artifact freezing followed a broken `.venv` symlink. Planning now carries the
  exact weak-agent budget and compact-task constraints; artifact freezing skips symlink mode
  mutation while hashing the link target. The combined ten-seed-through-first-mutation
  campaign contract remains covered by this unchecked item.
- [ ] Real same-track DeepSeek and Qwen patch/replay contracts prove that an achievable
  environment failure can produce guidance, exact replay uses the frozen Terra bundle,
  and admission still requires improvement without regression.
  DeepSeek is complete: Terra turned the missing exact launcher into well-formed failure
  JSON, Docker produced P1 fail/P2 pass, the same DeepSeek model patched the skill with a
  general symbolic-link repair, and fresh exact replay produced P1 pass/P2 pass and was
  admitted. Evidence is under
  `out/live-contracts/patcher/deepseek-v4-flash/20260721T171301Z-1fae1267/` and
  `out/live-contracts/exact-replay/deepseek-v4-flash/20260721T171703Z-51a2cfd1/`.
  Qwen repaired this environment at S0, so no failure or patch was scientifically
  warranted. Its earlier arithmetic patch/replay contract remains valid, but the literal
  Qwen environment-failure patch/replay subcase is not complete and must not be forced by
  relabeling the successful S0 run.
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
