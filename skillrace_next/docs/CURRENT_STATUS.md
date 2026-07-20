# Current Status and Known Issues

Date: 2026-07-20

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

The component and single-campaign pipeline is ready. The following work remains for the
actual multi-replicate study.

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
- [ ] Choose the model tracks, iteration budgets, held-out repetitions, and replicate
  count for the full headline study after inspecting the pilot. Use the same cheap model
  for every non-verifier role within one track.
- [ ] Create one frozen campaign config per selected skill/scenario and model track, with
  separate input and output roots. The replicate loop creates numbered replicate
  directories inside that campaign output.
- [ ] Start with a bounded pilot using about five Part I skills and two or three Part II
  scenarios. Run the frozen `pilot-v2` schedule and preserve the interrupted original
  pilot output. This is a new corrected pilot, not a retry of an unfavorable scientific
  outcome.
- [ ] For the pilot, start with a 10-minute wall timeout for weak-agent execution and its
  post-patch replay, and a 5-minute timeout for Codex checker authoring. Keep turn budgets
  separate from wall-clock timeouts. Confirm or adjust the proposer, generator, patcher,
  Docker-build, and executable-check limits from observed pilot evidence before the full
  study. The current development fixture uses 3 minutes for Pi execution/replay, 5 minutes
  for Codex, 5 minutes for patching, 3 minutes for Docker build, and 1 minute for checks.
- [ ] Manually inspect the pilot's first proposer output, generated S0, episode/tree merge,
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
behavior, and generic runtime image are complete. Experiment input preparation, pilot,
full study, and simple final aggregation remain. The study will run `skillrace_next`
directly; legacy cutover is not planned. More operational detail is in
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
