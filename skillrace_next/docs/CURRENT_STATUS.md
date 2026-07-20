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

- [ ] Make `replicate_count` execute the direct sequential loop described above. Verify
  that the expected numbered directories exist, inputs are identical, and no state is
  shared across replicates.
- [ ] Make CLI `--live` and `--scenario` overrides explicit. Warn on disagreement and
  freeze the effective values used by the command.
- [ ] Rename/rebuild the shared Pi runtime image with a generic, model-independent name.
  Preserve the old and new image IDs in the evidence so the rename does not obscure
  provenance.

Use a focused failing test for each implementation item. Do not introduce a scheduler,
matrix engine, workflow framework, compatibility layer, or general configuration system.
See [SkillRACE Next Handoff](HANDOFF.md#0-resolve-replicate-and-matrix-execution).

### Part I preparation

- [ ] Inspect `skills/` and select the 30 skills that best fit the Part I experiment.
  Record the selection criteria and final ordered list; do not alter the selected S0
  contents.
- [ ] Create or verify the provenance receipt for each selected S0. The receipt must bind
  the exact original skill tree/hash used by every discovery method.
- [ ] Create an ordered property file for each selected skill. These properties describe
  what the three Part I test creators should investigate.

### Part II preparation

- [ ] Select the Part II scenarios from `scenarios/` and make each public scenario
  description sufficiently specific to define the desired artifact and architecture.
  Pre-authored development NL checks are not required: Random, VeriGrey, and SkillRACE
  generate their own development prompt, Docker environment, and NL checks from this
  public scenario.
- [ ] Audit the existing final tests and executable checks for each selected scenario.
  Decide which are strong enough to use as held-out tests and replace or strengthen weak
  ones before the final run.
- [ ] Package every selected held-out test as `skillrace-test-case/1`, preserving its
  prompt, Docker environment, fixed NL checks, hashes, and receipt. These are Part II
  tests. Under the current verifier contract, the NL checks state the required behavior
  and real Codex authors the executable scripts from them at evaluation time.
- [ ] Freeze the held-out definitions before the final experiment begins. They may be
  loaded only after all methods produce their final skills, but creating or changing them
  after seeing those skills would make them no longer scientifically held out.

### Pilot and full execution

- [ ] Choose the model tracks, iteration budgets, held-out repetitions, and replicate
  count. Use the same cheap model for every non-verifier role within one track.
- [ ] Create one frozen campaign config per selected skill/scenario and model track, with
  separate input and output roots. The replicate loop creates numbered replicate
  directories inside that campaign output.
- [ ] Start with a bounded pilot using about five Part I skills and two or three Part II
  scenarios.
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

The pinned Pi base image/tag and OCI metadata still mention `deepseek-v3.2` while the
same runtime image is used for Lab models through mounted `models.json`. The image ID is
recorded and behavior is correct, but the naming is confusing. Rename/rebuild it with a
generic name that contains no model ID, preserving both image IDs in the evidence.

### End-of-study aggregation

The analysis modules compute metrics during pipeline completion. The CLI `analyze`
command only copies one existing summary into `analysis.json`. After the experiment
output layout is final, add one simple Python script that reads the completed campaign
summaries and computes the required cross-run totals, averages, and comparisons. Do not
build an analysis framework or incomplete-run recovery system.

## Handoff

The component and single-campaign implementation tasks are complete. The direct
replicate loop, explicit CLI override behavior, generic runtime image name, experiment
input preparation, pilot, full study, and simple final aggregation remain. The study will
run `skillrace_next` directly; legacy cutover is not planned. More operational detail is
in [SkillRACE Next Handoff](HANDOFF.md).

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
