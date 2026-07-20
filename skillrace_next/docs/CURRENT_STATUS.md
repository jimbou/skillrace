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

### Required implementation work

- [ ] Make replicate execution authoritative. The current CLI runs one campaign per
  invocation and does not expand `replicate_count`. Implement the smallest direct
  sequential replicate loop, or use an explicit one-replicate-per-command launcher that
  verifies every expected cell and gives each one a unique `experiment_id` and
  `output_root`. Do not set `replicate_count > 1` and assume it ran multiple campaigns.
- [ ] Reject a paid command when config `live` disagrees with the presence of `--live`.
  Until this is implemented, set config `live` to `true` whenever passing `--live`.
- [ ] Reject Part II when frozen config `scenario_path` differs from `--scenario`. Until
  this is implemented, pass the identical path in both places.

Use focused failing tests for each implementation item. Keep replicate execution direct
and sequential; do not introduce a scheduler, matrix engine, or workflow framework. See
[SkillRACE Next Handoff](HANDOFF.md#0-resolve-replicate-and-matrix-execution).

### Required experiment preparation and execution

- [ ] Select the real Part I S0 skills, Part II public scenarios, held-out tests, model
  tracks, iteration budgets, held-out repetitions, and replicate count.
- [ ] Create or verify every Part I S0 provenance receipt and ordered property file.
- [ ] Convert each selected held-out test to a strict `skillrace-test-case/1` record while
  preserving its prompt, Docker environment, NL checks, hashes, and receipt.
- [ ] Create frozen per-cell experiment configs with separate input and output roots.
- [ ] Run a bounded pilot and manually inspect the first proposer output, generated S0,
  episode/tree merge, generated skill, patch, Codex checker bundle, Docker result, and
  replay result for semantic correctness.
- [ ] Run the full study without retrying unfavorable scientific outcomes, verify every
  expected cell exists, scan evidence for credentials, and confirm no owned Docker
  container remains.
- [ ] Combine the per-campaign summaries into the final study report. The existing
  `analyze` command does not aggregate multiple cells, so use an explicit external
  aggregation procedure or implement a direct aggregator after defining its exact report.

### Optional, separately authorized, or deferred

- [ ] Rename/rebuild the runtime image if removing the historical `deepseek-v3.2` label is
  worth changing image references. Preserve the existing image-ID evidence.
- [ ] Refactor large modules only when a concrete future change creates a clear boundary;
  line count alone is not a reason.
- [ ] Perform the legacy package rename/cutover only after explicit user approval. It is
  not required to run `skillrace_next` experiments.

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

## P2: lower-priority clarity and maintainability

### P2-1: large modules

`pipeline/stages.py`, `methods/skillrace.py`, and `runtime/pi.py` exceed the design's
rough 400-line preference. Do not split them solely by line count. If Task 16 fixes make
one file hold two clearly distinct responsibilities, extract only that concrete boundary.

### P2-2: runtime image name retains the old development model label

The pinned Pi base image/tag and OCI metadata still mention `deepseek-v3.2` while the
same runtime image is used for Lab models through mounted `models.json`. The image ID is
recorded and behavior is correct, but the naming is confusing. Rename/rebuild only after
the functional gates are green, preserving the content hash/image ID evidence.

### P2-3: `analyze` is intentionally thin

The analysis modules compute metrics during pipeline completion. The CLI `analyze`
command only copies an existing summary into `analysis.json`; it does not aggregate cells
or repair incomplete runs. Expand it only if the final CLI contract requires a concrete
additional report.

## Handoff

The component and single-campaign implementation tasks are complete. Replicate expansion
and the two config/CLI consistency checks above remain before an automated
multi-replicate study. Full experiment input selection, conversion of any legacy
held-out records, actual study execution, optional analysis aggregation, and the
separately authorized legacy cutover are described in
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
