# Current Status and Known Issues

Date: 2026-07-18

## Executive status

- Tasks 1–14: implemented, individually tested, and committed.
- Task 15: implemented, live-tested, and committed as `38cc6a6`.
- Lab provider integration: implemented and committed.
- Task 16 verification and final gate: green; concrete arbitrary-campaign CLI composition
  remains incomplete.
- Final package rename/cutover: not authorized and not performed.

The individual component live contracts are green. The standalone two-model exact replay
and Part II contracts are green. The final combined gate passed both tracks on
2026-07-18. The remaining blocker is the input/composition contract for arbitrary
user-supplied Part I and Part II CLI campaigns.

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
- Held-out summaries include S0, per-test/all-tests rates, scenario mean/median, pairwise
  outcomes, regressions from S0, revision counts, and costs.

## Final gate result

The 2026-07-18 dual-model gate passed both parameterized cases in 24 minutes 53 seconds.
Both tracks completed fresh direct/Pi preflights and independent bounded Part I/Part II
slices. DeepSeek recorded Random `accepted, rejected`; Qwen recorded `retained, rejected`.
Both are coherent with their actual checker and replay evidence. Exact-key scans were
clean and no Docker containers remained.

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

### P0-3: CLI does not run supplied campaigns

`part1` and `part2` currently only freeze configs. With `--live`, they run hard-coded
bounded pytest files. They do not load a supplied suite/scenario, construct the concrete
stage callbacks, and call `run_part1`/`run_part2`.

Required fix:

- add the smallest direct composition functions for the two existing loops;
- read the already-defined suite/scenario inputs without a registry/factory;
- invoke the appropriate loop from the CLI;
- retain `live-smoke` for bounded contracts; and
- add an offline tiny CLI campaign proving outputs are derived from the supplied config,
  not a fixed pytest fixture.

Do not solve this with a workflow engine, service layer, plugin system, or generic
orchestrator.

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

### P1-3: invalid proposals do not become missed slots

The validator returns `invalid_test`, and Random permits one replacement. The generic
Part I/II loops currently assume selection returned a runnable test and do not record a
missed slot after the second invalid proposal. A production composition would raise from
`run_agent` instead of continuing the scientific budget correctly.

Add a direct conditional in the loop/composition: persist the invalid proposals, count a
missed slot, and continue without counting an agent execution.

### P1-4: Part I live campaign does not exercise confirmation and repair

The latest bounded Part I fixture used a trivial task that all methods passed, producing
zero candidates and no patch. It proves the three real execution/check/state paths and
immutable S0, but not group → reproduce → patch → replay inside the assembled Part I
campaign.

Individual patch/replay contracts exist, and offline Part I grouping tests exist, but one
bounded assembled Part I live fixture should exercise a confirmed failure and independent
repair without making later discovery use the patch.

### P1-5: production suite/check composition is still test-local

Part II's predefined check binding, held-out loading, and method selection are implemented
as callbacks in tests. Part I's verifier workspace preparation, confirmation, and repair
composition are also test-local.

Move only the concrete compositions required by the CLI into `skillrace_next`. Do not
hide the loops behind a generalized callback registry.

### P1-6: rerun fresh offline and component verification

After P0/P1 fixes:

1. Run every focused failing test first.
2. Run all unit/integration tests.
3. Run each affected individual live contract separately.
4. Inspect and preserve new semantic evidence.
5. Run the final two-model gate once.
6. Review forbidden architecture and legacy imports.
7. Commit only focused Task 16 changes.

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

## Files currently uncommitted for Task 16

At the time of this status document, Task 16 has scoped changes in:

- `skillrace_next/cli.py`;
- `skillrace_next/README.md` and updated status documents;
- `tests_next/unit/test_cli.py`;
- `tests_next/unit/test_documented_cli.py`;
- `tests_next/live/test_part1_tiny_live.py`;
- `tests_next/live/test_part2_tiny_live.py`;
- `tests_next/live/test_exact_replay_live.py`; and
- `tests_next/live/test_dual_model_gate_live.py` plus its gate-safety unit test.

Do not include unrelated dirty worktree files in Task 16 commits.

## Completion criteria

Task 16 is complete only when:

- the P0 issues are resolved;
- the concrete CLI actually invokes the supplied Part I/Part II campaign;
- affected offline and individual live contracts are fresh and green;
- the chosen final-gate criterion is explicit and passes both model tracks without
  retries for favorable behavior;
- evidence is sanitized and linked from terminal receipts;
- no owned Docker containers remain;
- legacy import and forbidden-architecture searches are clean;
- focused Task 16 commits contain only `skillrace_next/` and `tests_next/`; and
- the user separately approves any later package rename/cutover.
