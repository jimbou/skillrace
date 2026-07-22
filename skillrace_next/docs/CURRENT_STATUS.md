# Current Status and Known Issues

Date: 2026-07-21

## Executive status

- Tasks 1–14: implemented, individually tested, and committed.
- Task 15: implemented, live-tested, and committed as `38cc6a6`.
- Lab provider integration: implemented and committed.
- Task 16: production composition, CLI contracts, and final two-model gate green.
- Final package rename/cutover: not authorized and not performed.

The lack of cutover is not an execution blocker. Use `python -m skillrace_next` now and
pass existing `skills/...` and `scenarios/...` paths as explicit inputs. Cutover means
only replacing the old Python package and canonical entry points. Existing held-out
assets are reusable, but their record file must use the strict
`skillrace-test-case/1` schema; older scenario-specific `test.json` formats are not
silently migrated.

The individual component live contracts are green. The standalone two-model exact replay
and Part II contracts are green. The final combined gate passed both tracks on
2026-07-18. The explicit input contract is implemented: Part I receives S0,
provenance, identity, and properties; Part II receives a public scenario and deferred
held-out records, while every method creates its own development tests.

The production Part II CLI contract generated six development tasks. Random's first
task exposed a missing-input failure; the same-track Pi patch made the general repair to
stop without writing when a required input is absent, exact replay changed the failure
to pass, and S0→S1 was admitted. All S0/final-skill hidden cells then passed. The active
credential scan was clean and no container remained.

## Open TODOs

The component and single-campaign pipeline is ready, but the full study is paused for the
approved development-test revisions: frozen full NL-check catalogs on every test, root
weak-agent task containers, explicit proposal sampling, paper-style VeriGrey seeding, and
ten-seed SkillRACE initialization. The authoritative ordered checklist is
[Full-Study Remaining TODO](FULL_STUDY_REMAINING_TODO.md). The following historical list
records preparation completed before those revisions.

### Decisions now settled

- Replicates will use one direct sequential loop. Replicate `0001`, `0002`, and so on
  receive separate numbered output directories. Every replicate starts from fresh input
  copies and has no access to another replicate's state or output.
- CLI arguments are authoritative when they duplicate config fields. `--live` determines
  whether paid work runs, and Part II `--scenario` determines the public scenario. If the
  supplied config disagrees, print a clear warning and freeze the effective CLI value so
  the saved provenance describes what actually ran. Do not leave a knowingly false value
  in frozen `config.json`.
- Part I will use the 30 best-fitting skills selected from the repository `skills/` tree.
- Part II will use selected scenarios from the repository `scenarios/` tree.
- Run only `skillrace_next`; keep the legacy `skillrace` package untouched and ignore it.
  No cutover or deletion is needed for the study.
- Do not refactor modules merely because they are long.

### Required implementation work

- [x] Make `replicate_count` execute the direct sequential loop described above. Verify
  that the expected numbered directories exist, inputs are identical, and no state is
  shared across replicates. Offline coverage and the real two-replicate DeepSeek/Codex/
  Docker contract are green; evidence is under
  `out/live-contracts/cli-replicates/deepseek-v4-flash/20260720T073745Z-0f3da0bc/`.
- [x] Make CLI `--live` and `--scenario` overrides explicit. Warn on disagreement and
  freeze the effective values used by the command. Offline tests cover both fields and
  both `live` directions. The real paid override contract is green under
  `out/live-contracts/cli-replicates/deepseek-v4-flash/20260720T075536Z-5f9f9295/`.
- [x] Rename/rebuild the shared Pi runtime image with a generic, model-independent name.
  Preserve the old and new image IDs in the evidence so the rename does not obscure
  provenance. The new tag is `skillrace/pi-runtime:0.73.1`; build provenance is under
  `out/live-contracts/pi-runtime-image/20260720T080623Z-08a6e6aa/`. Fresh real Pi runs
  passed for DeepSeek and Qwen under `out/live-contracts/lab-provider/`.

Use a focused failing test for each implementation item. Do not introduce a scheduler,
matrix engine, workflow framework, compatibility layer, or general configuration system.
See [SkillRACE Next Handoff](HANDOFF.md#0-resolve-replicate-and-matrix-execution).

### Part I preparation

- [x] Inspect `skills/` and select the 30 skills that best fit the Part I experiment.
  Record the selection criteria and final ordered list; do not alter the selected S0
  contents. The fixed rule, ordered 30, and seven exclusions are recorded in
  `skillrace_next/study/part1/selection.json`.
- [x] Create or verify the provenance receipt for each selected S0. Every receipt under
  `skillrace_next/study/part1/<skill>/s0-receipt.json` binds the exact unchanged source
  tree and `SKILL.md` hashes plus the prepared property hash. Run
  `verify_part1_study` immediately before each study launch so later source edits fail
  closed.
- [x] Create an ordered property file for each selected skill. Existing ordered
  `id`/`nl` properties were normalized directly to the clean-room `P1...Pn` contract.
  Focused properties were authored for the two selected skills that lacked them,
  `file-check` and `js-feature`.

The real prepared-input contract passed with DeepSeek v4 Flash, Terra/medium Codex, and
Docker at
`out/live-contracts/part1-study-inputs/deepseek-v4-flash/20260720T081754Z-03ff7db6/`.
Manual inspection confirmed a semantically valid proposal, exact artifact creation and
read-back, an appropriately uncovered trace-only property, an exact-content checker,
authoritative Docker pass JSON, unchanged artifact hash, and container removal.

### Part II preparation

- [x] Select the Part II scenarios from `scenarios/` and make each public scenario
  description sufficiently specific to define the desired artifact and architecture.
  Pre-authored development NL checks are not required: Random, VeriGrey, and SkillRACE
  generate their own development prompt, Docker environment, and NL checks from this
  public scenario. The repository defines exactly ten D2 scenarios; all ten passed the
  target-purpose/rubric sufficiency check and are frozen under
  `skillrace_next/study/part2/`.
- [x] Audit the existing final tests and executable checks for each selected scenario.
  All 100 tests were accepted because their bound oracle evidence records a passing
  reference, rejected starting state, killed assigned negative implementations, and no
  survivors. The bundle preserves all 192 original scripts and their hashes as audit
  provenance; none is supplied to the runtime agent or used instead of Codex.
- [x] Package every selected held-out test as `skillrace-test-case/1`, preserving its
  prompt, Docker environment, fixed NL checks, hashes, and receipt. These are Part II
  tests. Under the current verifier contract, the NL checks state the required behavior
  and real Codex authors the executable scripts from them at evaluation time. Existing
  artifact-readable campaign properties became the fixed NL checks; trace-only properties
  are intentionally excluded from final artifact scoring.
- [x] Freeze the held-out definitions before the final experiment begins. They may be
  loaded only after all methods produce their final skills, but creating or changing them
  after seeing those skills would make them no longer scientifically held out. The
  self-contained manifest binds every record, receipt, public scenario, prompt,
  environment, NL-check file, source contract, candidate, oracle validation, and source
  checker. `verify_part2_study` rejects drift without depending on the mutable source
  `scenarios/` tree.

The prepared Part II contract passed at
`out/live-contracts/part2-study-inputs/deepseek-v4-flash/20260720T084119Z-9a9369c2/`.
Manual inspection confirmed semantic S0 generation, a concrete development proposal,
authoritative Docker failure, a same-track unchanged patch correctly rejected as
`patch_invalid`, deferred held-out loading, real Codex checkers, and conclusive hidden
results. The independent S0 held-out run failed one brace-preservation check while the
same retained S0 passed on the Random-labeled stochastic repetition; no revision was
falsely admitted. Credentials were absent and all three owned containers were removed.

### Pilot and full execution

- [x] Freeze the approved bounded pilot schedule. It uses DeepSeek v4 Flash, five Part I
  skills, three Part II scenarios, two iterations per method, one replicate, one held-out
  repetition, and only `t1` for pilot held-out evaluation. The eight hash-bound configs
  and explicit sequential commands are under `skillrace_next/study/pilot/`.
- [x] Run and inspect the first four Part I pilot cells far enough to identify whether the
  development-test contract is scientifically sound. `file-check` completed with one
  confirmed failure and a rejected patch. `js-feature` completed, but its accepted patch
  responded to an NL-check condition that the visible task had not requested.
  `csv-workbench` generated tasks that promised nonexistent `/mnt/data` fixtures, and
  `fix-failing-test` generated tasks that promised nonexistent projects in an empty
  workspace. The latter cell was interrupted before repair and has a terminal failure
  receipt. These are pilot-design findings, not method-quality results.
- [x] Correct the development-test grounding failure before further paid cells. All three
  proposers now state that the task starts with an empty `/workspace`, require inline
  inputs and `/workspace` paths, and forbid checks from adding hidden requirements.
  Deterministic validation rejects `/mnt/data` and `/tmp` tasks before Docker. The Codex
  guide classifies missing promised inputs and checker dependencies as inconclusive, and
  the pinned task image provides pytest to restricted checker users. Fresh evidence is:

  - `out/live-contracts/test-proposer/20260720T144717Z-d15d3f71/`;
  - `out/live-contracts/codex-verifier/20260720T144823Z-f1e8229b/`; and
  - `out/live-contracts/check-executor/20260720T145025Z-9e501a70/`.

  Manual inspection confirmed a self-contained DeepSeek proposal, prompt-matched Codex
  scripts with no Docker command, two authoritative Docker passes, unchanged artifact
  hash, credential-free evidence, and container removal. The task image used by the
  checker execution is pinned in evidence as
  `sha256:657012cd9be070f55fcff63ff7a7abcc97dc73e08c09bc804de8880437a3feef`.
- [x] Freeze the corrected pilot as `pilot-v2`. Its eight configs keep the approved
  inputs and budgets but use new experiment IDs and roots under
  `out/live-contracts/pilot-v2/`. The manifest and exact sequential commands are under
  `skillrace_next/study/pilot-v2/`; the original pilot configs and evidence are unchanged.
- [x] Preserve the terminal `pilot-v2` cell-1 infrastructure failure. All six discovery
  runs produced authoritative checker results, but the second SkillRACE tree alignment
  returned a semantic answer wrapped in prose and a JSON fence. The alignment parser had
  no bounded correction loop, so the CLI wrote `status: failed` and stopped before
  grouping or repair. No scientific outcome is reported for this cell and it will not be
  resumed or retried in the same output root.
- [x] Give tree alignment the same one-correction format contract as other model-authored
  JSON components. The first malformed, structurally invalid, or unknown-parent response
  is rejected; a second invalid response fails the component. The separate real DeepSeek
  v4 alignment passed and was manually inspected under
  `out/live-contracts/tree-merger/20260720T151700Z-595f4d98/`.
- [x] Freeze `pilot-v3` after both pilot infrastructure corrections. It keeps the approved
  inputs, model, methods, and budgets, with fresh experiment IDs and output roots under
  `out/live-contracts/pilot-v3/`. The manifest and exact sequential commands are under
  `skillrace_next/study/pilot-v3/`.
- [x] Complete and audit `pilot-v3` Part I cell 1, `file-check`. All six development
  prompts were self-contained under `/workspace`; every checker execution passed with an
  unchanged artifact; there were no candidates, inconclusives, invalid proposals, or
  patches. All six containers were removed, exact-key scans were clean, and none of the
  60 Codex exploratory commands invoked Docker. The terminal summary is under
  `out/live-contracts/pilot-v3/deepseek-v4-flash/part1/file-check/`.
- [x] Complete and audit `pilot-v3` Part I cell 2, `js-feature`. Thirteen checker results
  passed and three were inconclusive; there were no failures, candidates, invalid
  proposals, or patches. The inconclusive results came from one generated prompt that
  explicitly required `/usr/bin/node` although the task image provides Node at
  `/usr/local/bin/node`. The agent discovered the available command and completed the
  artifact, while Codex correctly treated the missing requested runtime path as an
  environment problem rather than a skill failure. All artifacts were unchanged, all six
  containers were removed, exact-key scans were clean, and none of 104 Codex exploratory
  commands invoked Docker. Evidence is under
  `out/live-contracts/pilot-v3/deepseek-v4-flash/part1/js-feature/`.
- [x] Preserve `pilot-v3` Part I cell 3, `csv-workbench`, as invalid pilot evidence. One
  VeriGrey proposal was an unrelated arithmetic task rather than a CSV task. Its weak
  agent completed the visible task and observed `84`, but the generated Python checker
  compared the real newline with a literal backslash-plus-`n`, falsely reported failure,
  and caused an invalid patch attempt. The other confirmed CSV failure and both rejected
  patch outcomes remain preserved, but no method-quality result is reported for this
  cell. The terminal evidence remains under
  `out/live-contracts/pilot-v3/deepseek-v4-flash/part1/csv-workbench/`.
- [x] Correct the two cell-3 infrastructure failures without adding a new pipeline layer.
  All three proposer prompts now require the generated task to exercise the supplied
  skill; branch or tool-transition coverage cannot substitute for relevance. The Codex
  guide now marks an unrelated task `uncovered` and requires checker decision expressions
  to agree with their observed pass conditions, including correct newline escaping.
  The fresh DeepSeek proposer contract passed under
  `out/live-contracts/test-proposer/20260720T162920Z-4106ddb8/`. Terra/medium produced
  semantically correct checks for a relevant artifact under
  `out/live-contracts/codex-verifier/20260720T162942Z-6ce60058/`, then correctly marked an
  arithmetic task against a CSV skill as uncovered under
  `out/live-contracts/codex-verifier-relevance/20260720T163201Z-94253818/`. Exact-key
  scans were clean and none of the Codex commands invoked Docker.
- [x] Freeze the corrected schedule as `pilot-v4`, retaining the approved eight cells,
  model, methods, inputs, budgets, and held-out policy while assigning fresh experiment
  IDs and output roots under `out/live-contracts/pilot-v4/`. The hash-bound schedule and
  exact sequential commands are under `skillrace_next/study/pilot-v4/`.
- [x] Complete and audit `pilot-v4` Part I cell 1, `file-check`. All six generated tasks
  meaningfully exercised exact file creation and used only self-contained `/workspace`
  inputs. Ten executable checks passed; five execution-history properties were explicitly
  uncovered rather than guessed. There were no failures, candidates, inconclusives,
  invalid proposals, or patches. Manual inspection found coherent episode segmentation;
  the second run was retained as a separate root-aligned branch. All artifacts were unchanged,
  all cleanup receipts succeeded, exact-key scans were clean, none of 72 Codex commands
  invoked Docker, and no owned container remained. Evidence is under
  `out/live-contracts/pilot-v4/deepseek-v4-flash/part1/file-check/`.
- [x] Complete and audit `pilot-v4` Part I cell 2, `js-feature`. All six prompts were
  self-contained JavaScript implementation/testing tasks that meaningfully exercised the
  skill. Discovery produced 11 passes and three failures. Exact confirmation retained
  three distinct `validation_missing` bugs: two on one SkillRACE `deepClone` artifact and
  one on a VeriGrey `findMissingNumber` test. DeepSeek produced three small, relevant
  skill patches, but exact replay left each targeted failure unchanged, so all three were
  correctly rejected. There were no inconclusives, infrastructure failures, or invalid
  proposals. Manual inspection confirmed that the checkers enforced visible requirements
  and that all patch diffs addressed the observed missing test coverage without weakening
  checks. All 12 discovery/confirmation/replay artifacts were unchanged, all 12 cleanup
  receipts succeeded, exact-key scans were clean, none of 94 Codex commands invoked
  Docker, and no owned container remained. Evidence is under
  `out/live-contracts/pilot-v4/deepseek-v4-flash/part1/js-feature/`.
- [x] Preserve `pilot-v4` Part I cell 3, `csv-workbench`, as terminal invalid pilot
  evidence. The first Random task contradicted its six-row fixture by saying `Widget C`
  appeared once although it appeared twice; Terra chose the concrete rows and passed the
  result instead of treating the generated contract as ambiguous. After three completed
  discovery runs, the second VeriGrey proposal returned valid JSON inside a Markdown
  fence. VeriGrey had no bounded format correction, so the CLI wrote `command.json` with
  `status: failed` and stopped. No method-quality outcome is reported, the output root is
  not resumed, all three containers were removed, and no owned container remains.
  Evidence is under
  `out/live-contracts/pilot-v4/deepseek-v4-flash/part1/csv-workbench/`.
- [x] Correct both cell-3 infrastructure findings directly. VeriGrey proposals now receive
  at most one second Pi call after malformed JSON, using a fresh attempt directory and an
  explicit raw-JSON correction prompt; a second malformed response fails. All proposers
  now require their inline data, examples, prose, and expected values to be internally
  consistent. The Codex guide classifies mutually inconsistent generated tasks as
  inconclusive or uncovered rather than choosing an interpretation. The full offline suite
  is green. A fresh relevant and arithmetically consistent DeepSeek v4 VeriGrey proposal
  passed under
  `out/live-contracts/verigrey/deepseek-v4-flash/20260720T174026Z-97c3bd60/`.
  Terra/medium again rejected an unrelated task as uncovered under
  `out/live-contracts/codex-verifier-relevance/20260720T174135Z-4924c63c/`; none of its 16
  commands invoked Docker. Exact-key scans were clean and no owned container remained.
- [x] Freeze `pilot-v5` with the same approved eight cells, inputs, model assignments,
  methods, budgets, and held-out policy, but fresh experiment IDs and roots under
  `out/live-contracts/pilot-v5/`. Its hash-bound schedule and exact sequential commands
  are under `skillrace_next/study/pilot-v5/`.
- [x] Complete and audit `pilot-v5` Part I `csv-workbench`. All six discovery runs
  completed. Authoritative Docker execution produced seven passes, two failures, and two
  inconclusive results with unchanged artifacts. Exact confirmation retained one
  `validation_missing` bug. DeepSeek's patch improved one prior failure but regressed a
  prior pass, so the no-regression admission rule correctly rejected it and retained S0.
  Manual inspection found one minor non-operative row-order typo and two material
  arithmetic/median contradictions in generated prompts; Terra/medium classified both
  material contradictions as inconclusive instead of turning them into skill outcomes.
  This is useful pipeline-safety evidence, but generated-task consistency is still a
  pilot-quality limitation and this cell alone is not a headline method comparison. The
  episode chain was coherent, the second SkillRACE run was correctly root-aligned, all
  nine cleanup receipts succeeded, exact-key scans were clean, none of 140 Codex commands
  invoked Docker, and no owned container remained. Evidence is under
  `out/live-contracts/pilot-v5/deepseek-v4-flash/part1/csv-workbench/`.
- [x] Preserve `pilot-v5` Part I `fix-failing-test` as a terminal parser failure. The
  first Random slot reached its one allowed replacement. Both replacement responses were
  valid proposal JSON inside one outer JSON fence, with Python code fences inside the
  JSON prompt string. The parser's outer-fence allowance incorrectly counted the inner
  prompt fences and rejected both responses as malformed. The CLI wrote `status: failed`;
  no weak-agent or checker run started, and the output root will not be resumed.
- [x] Correct the nested-fence parser defect directly. Random proposal parsing now removes
  its already-supported single outer JSON fence without counting code fences contained
  inside the JSON string. The focused regression test failed before the one-line fix;
  the Random unit file and full offline suite are green. A fresh real DeepSeek v4 proposal
  passed deterministic validation under
  `out/live-contracts/test-proposer/20260720T181555Z-787d16a4/`. `pilot-v6` freezes the
  same eight scientific cells with new IDs and output roots; only cells lacking valid
  prior results should be launched.
- [x] Complete and audit `pilot-v6` Part I `fix-failing-test`. All six self-contained
  development tasks exercised implementation-only test repair; 13 authoritative Docker
  checks passed with no failures, inconclusives, candidates, or patches. One SkillRACE
  seed prompt contained confusing self-correcting comments that called already-correct
  functions buggy, but its single executable factorial defect and expected tests remained
  unambiguous; the agent fixed only that defect. Manual inspection confirmed coherent
  episode chains and correct root alignment of the independent second SkillRACE run. All
  artifacts were unchanged, all six cleanup receipts succeeded, exact-key scans were
  clean, none of 117 Codex commands invoked Docker, and no owned container remained.
  Evidence is under
  `out/live-contracts/pilot-v6/deepseek-v4-flash/part1/fix-failing-test/`.
- [x] Complete and audit `pilot-v6` Part I `regex-expert`. Six relevant tasks produced
  ten discovery passes, three failures, and one inconclusive result. Exact confirmation
  retained all three failures: one validator accepted Unicode digits and trailing-newline
  input, while two accepted trailing-newline input because Python `$` is not an absolute
  end anchor. DeepSeek produced three general skill edits; exact replay admitted the one
  that changed the targeted failure to pass without regressing either prior pass, and
  rejected the two whose failure remained. The inconclusive result came from a generated
  NL check that required an embedded-code output line incompatible with the visible
  prompt's exact success format; Terra/medium correctly prevented it from becoming a
  skill outcome. Manual inspection confirmed coherent episode chains, root alignment,
  checker semantics, all three patch diffs, and exact replays. All 12 artifacts were
  unchanged during checking, all cleanup receipts succeeded, exact-key scans were clean,
  none of 135 Codex commands invoked Docker, and no owned container remained. Evidence is
  under `out/live-contracts/pilot-v6/deepseek-v4-flash/part1/regex-expert/`.
- [x] Preserve `pilot-v6` Part II `text-template` as invalid checker evidence. Random
  iteration 0 exposed a real triple-brace failure and produced a small general DeepSeek
  patch. The frozen checker also imported the first artifact's undeclared two-argument
  `render` function. The replay artifact used a valid three-argument helper behind the
  prompt-declared file/CLI workflow, so the hidden API assumption caused a false replay
  failure and invalidated the admission decision. The cell was interrupted during the
  next method's checker authoring to avoid further paid work; no owned container remained,
  and the root will not be resumed.
- [x] Correct the verifier hidden-API defect directly. The guide now forbids importing or
  calling artifact functions unless the prompt explicitly declares the interface and
  signature. Generalized probes must use a scratch artifact copy and the visible
  CLI/file workflow, or be marked uncovered. The focused guide test and full offline
  suite are green. Fresh Terra/medium authoring over a real Yunwu artifact passed under
  `out/live-contracts/codex-verifier/20260720T191247Z-dd0d3a62/`; its exact scripts then
  produced two authoritative Docker passes with unchanged artifact and successful cleanup
  under `out/live-contracts/check-executor/20260720T191416Z-882993d6/`. `pilot-v7`
  freezes the same scientific cells with fresh IDs and roots; only cells lacking valid
  prior results should be launched.
- [x] Complete and audit `pilot-v7` Part II `text-template`. All six development runs
  passed their generated checks, so no method requested a patch and all three final
  skills remained the generated S0. Held-out evaluation began only after development
  finished. The independent S0, Random, and SkillRACE runs passed all three frozen
  checks. The VeriGrey-labeled stochastic run failed the substitution check because its
  artifact used `\w+` and therefore did not replace requested keys containing a hyphen or
  space; its preservation and repetition checks passed. This is a valid held-out run
  outcome, not a checker defect or an admitted skill regression: every method still had
  the identical S0 hash. Manual inspection confirmed semantic S0 generation, relevant
  development tasks, visible CLI/file-based checkers, authoritative Docker results, and
  the legitimate VeriGrey artifact failure. All ten artifacts were unchanged during
  checking, all ten cleanup receipts succeeded, exact-key scans were clean, none of 199
  Codex commands invoked Docker, and no owned container remained. Evidence is under
  `out/live-contracts/pilot-v7/deepseek-v4-flash/part2/text-template/`.
- [x] Complete and audit `pilot-v7` Part II `csv-stats`. Five development runs passed;
  the remaining VeriGrey run was correctly inconclusive because its generated NL check
  simultaneously required seven listed records and six data rows. No confirmed failure
  was eligible for patching, so all three methods retained the generated S0. Held-out
  evaluation began only after all development runs finished, and the independent S0,
  Random, VeriGrey, and SkillRACE runs each passed both frozen properties. Two development
  checker bundles needed their one allowed Terra structure correction before Docker: one
  had invalid manifest fields and one omitted the manifest. Manual inspection confirmed
  a semantically complete generated skill, relevant self-contained CSV tasks, the valid
  contradiction ruling, visible CLI/file-based checkers, and authoritative Docker
  results. All ten artifacts were unchanged during checking, all ten cleanup receipts
  succeeded, exact-key scans were clean, none of 222 Codex commands invoked Docker, and
  no owned container remained. Evidence is under
  `out/live-contracts/pilot-v7/deepseek-v4-flash/part2/csv-stats/`.
- [x] Preserve `pilot-v7` Part II `fix-failing-test` as invalid workspace evidence. All
  six development runs passed, but the first held-out weak-agent container saw an empty
  `/workspace`: the durable empty host artifact mount had hidden the initial project
  baked into the validated test image. The agent consequently fabricated a different
  implementation and test suite, then repaired its own fabricated failure. Terra passed
  pytest but marked harness preservation uncovered even though the immutable environment
  Dockerfile defined the original files. The cell was interrupted during the next
  held-out checker to avoid further invalid paid work, no owned container remained, and
  the output root will not be resumed.
- [x] Correct held-out workspace initialization and preservation checking directly. Weak
  execution now copies the validated image's existing `/workspace` into the empty durable
  host artifact before mounting it back into the task container. Checker containers do
  not seed, so final artifacts remain immutable. The Codex guide now identifies the
  supplied environment Dockerfile and build context as the authoritative initial-workspace
  baseline for test or harness preservation. Focused tests failed before both changes;
  the surrounding runtime tests and full offline suite are green. A real DeepSeek v4
  task read and preserved a baked input under
  `out/live-contracts/task-runner-seeded/20260720T205131Z-2701a626/`. Terra/medium covered
  both output correctness and input preservation, with no Docker access, under
  `out/live-contracts/codex-verifier-baseline/20260720T205316Z-40dd77da/`; its two exact
  scripts then passed through authoritative Docker execution with unchanged artifact and
  successful cleanup under
  `out/live-contracts/check-executor-baseline/20260720T205633Z-590c27f6/`. Exact-key
  scans were clean and no owned container remained.
- [x] Preserve `pilot-v8` DeepSeek Part II `fix-failing-test` as development and timing
  evidence. Its six development slots completed without a confirmed failure and all
  methods retained S0. Its original held-out results used regenerated Terra checkers, so
  those scores are not authoritative benchmark results and are not reused in the full
  study. Commit `d75a27a` corrected production held-out evaluation to execute the frozen
  source checks directly. The corrected DeepSeek component contract is under
  `out/live-contracts/part2-study-inputs/deepseek-v4-flash/20260721T155550Z-2d5b846e/`.
- [x] Complete and audit the Qwen timing-pilot-v8 Part II cell after the held-out fix.
  One VeriGrey development slot executed; five development slots were recorded as invalid
  without retry. All four held-out labels used byte-verified frozen source checks through
  Docker, passed, kept their artifacts unchanged, invoked no held-out Codex process, and
  cleaned up successfully. Evidence is under
  `out/live-contracts/timing-pilot-v8/qwen3.6-flash/part2/fix-failing-test/`.
- [x] Freeze one 60-second weak task-execution and replay cutoff from 23 valid pilot runs
  (median 16.548s, p95 31.620s, maximum 33.287s). Proposal, patch-authoring, Docker,
  checker, and Terra limits remain separate. See
  `study/timing-pilot-v8/TIMING_ANALYSIS.md`.
- [x] Separate that cutoff in execution code. `timeouts.pi` now reaches only weak task
  execution and exact replay; non-task Pi calls use `timeouts.provider`, and patching
  continues to use `timeouts.patch`. A real DeepSeek proposal contract completed twice
  with a recorded 240-second provider-role limit while `timeouts.pi` was deliberately set
  to five seconds. Evidence is under
  `out/live-contracts/test-proposer/deepseek-v4-flash/20260721T172608Z-c047ce16/`.
- [x] Prove the root-agent environment-repair behavior. Qwen repaired a missing exact
  `/usr/bin/node` launcher by linking the installed `/usr/local/bin/node`, completed the
  requested command, and passed the authoritative checks. DeepSeek followed the deficient
  S0 guidance and used the alternate executable directly, producing the intended failure.
- [x] Correct the verifier distinction exposed by that DeepSeek run. A missing dependency
  needed only by a checker remains inconclusive, but a repairable exact launcher required
  by the task is a failure. Checker scripts must capture child exit 126/127 and emit one
  JSON object rather than leaking a raw shell exit.
- [x] Complete the DeepSeek environment-failure patch and exact replay contract. After a
  first preserved patch attempt merely restated the failure, the patcher prompt was
  narrowed to inspect the environment and trace for command-launch failures. The fresh
  patch instructed a general symbolic-link repair; exact replay changed P1 from fail to
  pass, retained P2 as pass, and was accepted. Evidence is under
  `out/live-contracts/patcher/deepseek-v4-flash/20260721T171301Z-1fae1267/` and
  `out/live-contracts/exact-replay/deepseek-v4-flash/20260721T171703Z-51a2cfd1/`.
  Qwen's S0 success is preserved as success, not converted into a patch opportunity.
- [x] Complete the two-call Random independence contract for both model tracks. Each model
  received byte-identical proposer inputs on its two independent calls, with no prior
  proposal state, and produced two valid tests under distinct receipts/output roots.
  Temperature and model provenance were correct and exact-key scans were clean.
- [ ] Complete the combined ten-seed-through-first-branch SkillRACE contract for Qwen.
  DeepSeek completed ten ordered seeds and the first tree-selected branch under
  `out/live-contracts/skillrace-ten-seed/deepseek-v4-flash/20260721T172930Z-31b0bfd6/`.
  Qwen repeatedly completed the real weak runs, Terra bundles, Docker checks, and tree
  updates, reaching seed nine in one preserved root. Episode creation now supplies the
  exact ordered relevant-event IDs and permits two correction calls. The exact previously
  failing nine-event Qwen trace passed live as five grounded episodes under
  `out/live-contracts/episode-creator/qwen3.6-flash/20260722T002556Z-d4041428/`.
  Trace splitting was not added because the observed failure was a field-name typo on a
  short trace. Initial materialization now also permits two correction calls. The next
  fresh combined run completed all ten ordered weak executions and tree updates, reaching
  branch phase with 48 nodes and 47 edges. The first branch mutator response then violated
  its contract: it relied on document size, added an undeclared field, and returned outer-
  fenced JSON containing embedded fences. Preserve
  `out/live-contracts/skillrace-ten-seed/qwen3.6-flash/20260722T002923Z-06536695/`
  and the earlier diagnostic roots.
  All SkillRACE model-authored boundaries now permit two correction calls after an invalid
  response: diversity planning, seed materialization, episode creation, ambiguous tree
  alignment, edge selection, and branch mutation. Selector correction stops as soon as a
  valid edge is chosen; mutator correction never reruns that selector. Weak-agent task
  execution is still exactly once.
  The next Qwen root,
  `out/live-contracts/skillrace-ten-seed/qwen3.6-flash/20260722T013522Z-e0d0a78f/`,
  exercised third-attempt seed materialization and episode creation, then stopped when all
  three seed-09 materializations were invalid. Its plan had requested an unnecessarily huge
  dictionary fixture. The plan/materializer prompts now require one focused task under the
  exact turn/time budget and compact repetitive fixtures. A smaller real initializer passed
  with a manually inspected feasible ten-task plan under
  `out/live-contracts/skillrace-initializer/qwen3.6-flash/20260722T021309Z-225f0507/`.
  A subsequent combined root completed nine seeds and entered seed ten, then hit a host
  artifact-freezing bug on a broken `.venv/bin/python` symlink before checks:
  `out/live-contracts/skillrace-ten-seed/qwen3.6-flash/20260722T021441Z-49fcfb86/`.
  `freeze_artifact` now preserves symlinks without following them, and `tree_hash` records
  their link targets; the focused regression and task-container integration tests pass.
  The corrected individual Qwen long-tree contract passed under
  `out/live-contracts/skillrace-edge-selector/qwen3.6-flash/20260722T025327Z-87914bff/`.
  It selected the unique promising edge once, retained an invalid first mutator attempt,
  corrected only the mutator, and produced a Docker-valid, semantically inspected task whose
  `/opt/report-tools/bin/reportgen` recovery path is absent from the visible prompt. The full
  combined Qwen eleven-execution contract remains incomplete; do not relabel these roots.
- [x] Choose the full-study scale: DeepSeek v4 Flash and Qwen 3.6 Flash tracks, 30
  development iterations, one held-out repetition, and one replicate per campaign. Every
  non-verifier role in a track uses that track's same model.
- [ ] Create one frozen campaign config per selected skill/scenario and model track, with
  separate input and output roots. The replicate loop creates numbered replicate
  directories inside that campaign output.
- [ ] Before freezing those configs, create one frozen base image per selected Part I skill
  and Part II scenario with the small tool set appropriate to that context. Record the image
  ID and expose its capability context to test generation. The current Python/Node/Bash/Perl
  capability wording is only for `skillrace-next/task-fixture:test`; replace it with each
  per-skill image's frozen context before the full study.
- [x] Finish the bounded pilot using the frozen v3-v8 cells. Preserve all
  interrupted predecessor outputs and do not resume a terminal output root. Starting a
  fresh root after a recorded infrastructure correction is not a retry of an unfavorable
  scientific outcome.
- [x] For the pilot, use a 10-minute wall timeout for weak-agent execution and its
  post-patch replay, and a 5-minute timeout for Codex checker authoring. Keep turn budgets
  separate from wall-clock timeouts. Pilot v7/v8 used 10 minutes for Pi execution/replay
  and patching, 5 minutes for Codex, 3 minutes for Docker build, and 1 minute for checks.
  These bounds completed the pilot without masking any agent timeout as infrastructure.
- [x] Manually inspect the pilot's first proposer output, generated S0, episode/tree merge,
  generated skill, patch, Codex checker bundle, Docker result, and replay result for
  semantic correctness.
- [ ] Run the full study without retrying unfavorable scientific outcomes. Verify every
  expected campaign/replicate exists, scan evidence for credentials, and confirm no owned
  Docker container remains.
- [ ] After the output structure is final, write one small Python aggregation script that
  reads all campaign summaries and reports the selected key metrics, averages, and
  comparisons across runs. This is end-of-study work; no aggregation framework is needed.

## Confirmed working behavior

### Clean-room boundary and records

- `skillrace_next` does not import the legacy package.
- Configs and all eight record types have strict `/1` schemas.
- Canonical JSON, file/tree hashes, atomic terminal JSON writes, and artifact freezing are
  covered offline.

### Providers and Pi

- Yunwu `deepseek-v3.2` remains supported.
- Lab `deepseek-v4-flash` and `qwen3.6-flash` work through direct and Pi calls.
- Friendly/upstream/provider-qualified names and usage are preserved.
- Every non-verifier role in a track uses the same configured cheap model.

### Task, verifier, Docker, and replay components

- Weak agents run in validated task images with a read-only installed skill.
- Artifacts and traces survive task execution.
- Codex Terra/medium authors checker scripts from local read-only inputs.
- The deterministic manifest validator requires every checker argv to invoke that
  check's declared script; malformed argv receives the one allowed Codex correction
  instead of reaching Docker.
- Checker scripts execute through `docker exec` and write authoritative JSON.
- Same-track Pi patching edits only the copied `SKILL.md`.
- Exact replay reuses the frozen checker scripts rather than asking Codex again.

### Part I and Part II loop semantics

- Part I checks immutable S0 identity on every discovery run and groups before repair.
- Part II copies one generated S0 per method, records each improvement step, carries only
  accepted candidates forward, retains rejected skills, and defers held-out loading.
- Part II has no pre-authored development suite. Random, VeriGrey, and SkillRACE create
  their prompt, environment, and NL check from the original public scenario and their
  own accumulated state.
- Patch admission requires at least one previously failing check to become passing and
  forbids every previously passing check from becoming failing. Other prior failures may
  remain failing. Retained-test checks must also remain passing.
- Held-out summaries include S0, per-test/all-tests rates, scenario mean/median, pairwise
  outcomes, regressions from S0, revision counts, and costs.

## Final gate result

The final 2026-07-18 dual-model gate passed both parameterized cases in 25 minutes 30
seconds. Both tracks completed fresh direct/Pi preflights and independent bounded Part
I/Part II slices. DeepSeek and Qwen each recorded Random `accepted, rejected`, retained
S1 after the rejection, and changed the held-out result from S0 fail to Random S1 pass.
This is bounded contract evidence, not a general method-quality conclusion. Exact-key
scans were clean and no Docker containers remained.

## P0: fix before another paid final gate

### Resolved: credential exposure and verifier environment

The Lab key was rotated. Gate helpers no longer receive raw secrets as normal arguments,
captured child output is redacted, and focused failure tests cover the behavior. Codex
removes both `yunwu_key` and `LAB_KEY_UNLIMITED` from its environment.

### Resolved: replay timeout and container cleanup

`agent_timeout` now preserves the partial artifact and executes the frozen checker bundle
without retry. Provider/container errors remain infrastructure failures. Task execution,
checker execution, and replay exception paths remove their containers and persist cleanup
receipts. Focused unit/integration tests cover each path.

### Resolved: production campaign CLI composition

`part1 --live` now calls the immutable-S0 campaign with explicit S0 directory, receipt,
skill ID, and property file arguments. `part2 --live` generates S0 from an explicit
scenario, lets each method generate its own development tasks, and opens repeatable
held-out `TestCase` records only after all methods finish. `live-smoke` remains the
separate bounded component runner.

### Resolved: stochastic final-gate criterion

The gate validates each observed transition against its input skill, original checker
results, patch attempt, replay result, and resulting version. It no longer requires every
model to produce `accepted, rejected`. The earlier and current individual live contracts
retain direct accepted-carry-forward evidence, and the final gate is never retried merely
to obtain a favorable transition.

## P1: complete before declaring Task 16 done

### Resolved: exception-safe task-container lifecycle

Direct cleanup now covers evidence-capture, checker-processing, and replay infrastructure
exceptions while preserving host evidence. No recovery framework or janitor was added.

### Resolved: failed gate evidence link

The gate resolves and returns the new child evidence directory independently of the child
exit status, then records it before raising. A focused failure test also proves captured
output is sanitized.

### Resolved: invalid proposals become missed slots

The validator returns `invalid_test`, and the proposer permits one replacement. If the
replacement is also invalid, Part I/II writes `missed-slot.json`, increments that method's
separate `invalid_proposal_count`, and continues without running the weak agent or
classifying a bug.

### Resolved: Part I grouping and repair boundaries

The bounded Part I live slice proves immutable S0 and the three real execution/check/state
paths. Focused offline integration proves all discovery completes before grouping and
patching. The separate real patcher and exact-replay contracts prove the repair boundary.
The final gate does not require a model to produce a favorable failure candidate merely
to force an assembled patch.

### Resolved: production stage composition

`pipeline/campaigns.py` directly binds proposal, weak execution, Codex authoring, Docker
execution, method-state updates, Pi patching, exact replay, and held-out evaluation to the
two existing sequential loops. Generated development tests are validated under the run
output root; external held-out records remain constrained to the configured suite root.

### Resolved: production command failure receipt

If a production Part I/II campaign raises, the exception remains visible and evidence
remains in place, while `command.json` records terminal status `failed`. The CLI then
re-raises the original exception.

### Resolved: fresh offline and live verification

The final Task 16 cycle ran focused red/green tests, all 155 unit/integration tests,
separate real production Part I/II CLI contracts, semantic evidence inspection, and the
final two-model gate. Legacy-import and forbidden-architecture searches were reviewed.

### Resolved: observed-edge SkillRACE selection and mutation

Post-seed SkillRACE no longer depends on synthetic `unreached` nodes. The host creates a
compact stable-ID index from real observed episode transitions. One fresh tool-free Pi
call selects a promising edge, the host isolates its branch deterministically, and a
second fresh tool-free Pi call generates the mutation from only that branch and the fixed
catalog. Long-tree live contracts passed with both `lab/deepseek-v4-flash` and
`lab/qwen3.6-flash`. Manual inspection confirmed that both selected the only meaningful
helper-path edge, made its fixed-path assumption fail, preserved a feasible local recovery
route, did not leak that route in the visible prompt, and produced Docker-valid tasks.

## Recorded lower-priority decisions

### No line-count refactor

`pipeline/stages.py`, `methods/skillrace.py`, and `runtime/pi.py` exceed the design's
rough 400-line preference. This is not a TODO. Do not split them solely by line count;
extract code only if a later concrete change creates a necessary boundary.

### Generic runtime image name

Resolved. The pinned shared tag is `skillrace/pi-runtime:0.73.1`, and final OCI metadata
records the catalog as `runtime-mounted`. The metadata-only rebuild used a hash-derived
local source tag, so it reused the existing Pi layers instead of repeating the expensive
npm/base build. Evidence preserves source image ID `sha256:64a4b2fb...f58a` and generic
runtime image ID `sha256:7d808680...65c8`. Real DeepSeek and Qwen Pi tool contracts both
passed on the generic image.

### End-of-study aggregation

The analysis modules compute metrics during pipeline completion. The CLI `analyze`
command only copies one existing summary into `analysis.json`. After the experiment
output layout is final, add one simple Python script that reads the completed campaign
summaries and computes the required cross-run totals, averages, and comparisons. Do not
build an analysis framework or incomplete-run recovery system.

## Handoff

The component, single-campaign implementation, direct replicate loop, CLI override
behavior, generic runtime image, experiment input preparation, and bounded pilot are
complete. The full study and simple final aggregation remain. The study will run
`skillrace_next` directly; legacy cutover is not planned. More operational detail is in
[SkillRACE Next Handoff](HANDOFF.md).

The repository still contains extensive unrelated dirty legacy work. Do not reset,
clean, reformat, or include those files in future `skillrace_next` commits.

## Task 16 completion record

- [x] P0 and P1 issues resolved.
- [x] Concrete CLI invokes the supplied Part I and Part II campaigns.
- [x] Affected offline and individual live contracts passed.
- [x] The explicit final-gate criterion passed both model tracks without retries for
  favorable behavior.
- [x] Evidence was sanitized and linked from terminal receipts.
- [x] No owned Docker container remained after final verification.
- [x] Legacy-import and forbidden-architecture searches were reviewed.
- [x] Task 16 commits contained only `skillrace_next/` and `tests_next/` changes.
