# Current Status and Known Issues

Date: 2026-07-18

## Executive status

- Tasks 1–14: implemented, individually tested, and committed.
- Task 15: implemented, live-tested, and committed as `38cc6a6`.
- Lab provider integration: implemented and committed.
- Task 16 CLI/docs/final gate: in progress and uncommitted.
- Final package rename/cutover: not authorized and not performed.

The individual component live contracts are green. The standalone two-model Part I
contract is green. Prior standalone Part II contracts demonstrated accepted carry-forward
and rejected-patch retention. The latest final combined gate is red for the reasons below.

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

The 2026-07-17 dual-model gate failed both Part II assertions after both models passed
provider/Pi preflight and Part I.

| Track | Actual Part II transitions | Meaning |
|---|---|---|
| DeepSeek V4 Flash | `rejected`, `rejected` | Patcher changed lower-middle to upper-middle; replay correctly rejected it |
| Qwen 3.6 Flash | `retained`, `rejected` | Agent ignored wrong S0 and already produced the correct first artifact |

The test expected `accepted`, `rejected` for both. This is a test-harness failure caused
by assuming one stochastic model trajectory. It is not evidence that the deterministic
pipeline accepted an invalid revision.

## P0: fix before another paid final gate

### P0-1: credential exposure and verifier environment

Two defects exist:

1. `test_dual_model_gate_live.run_slice` receives the raw Lab secret as a normal function
   argument. On failure, pytest included that argument in its traceback. The saved files
   were redacted, but terminal output was not.
2. `verification/codex.py::_invoke_codex` removes `yunwu_key` but leaves
   `LAB_KEY_UNLIMITED` in the inherited Codex environment.

Required fix:

- rotate the Lab key used by the failed gate;
- never pass raw secrets as pytest-visible helper arguments;
- centralize local redaction only as a small function, not a credential manager;
- remove every non-verifier provider credential from the Codex environment; and
- add focused tests that force a failure and prove neither captured output nor Codex env
  contains either credential.

### P0-2: replay timeout is not treated as behavioral evidence

`pipeline/stages.py::replay` currently raises when the fresh run status is not
`completed`. For `agent_timeout`, this prevents the frozen partial artifact from reaching
the checker. It can also bypass task-container cleanup.

This conflicts with the required scientific behavior: a weak-agent timeout is an
experimental execution. Preserve the trace/artifact, run the authoritative checks, and
let the failed/missing artifact inform patching or rejection.

Required fix:

- do not retry the weak agent;
- execute the saved checker bundle against the partial frozen artifact after
  `agent_timeout`;
- keep provider/container infrastructure failures distinct;
- guarantee container cleanup after durable evidence, including exception paths; and
- test timeout replay with a real/controlled task process and cleanup receipt.

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

### P0-4: final gate criterion is stochastic and overfitted

The final gate assumes the first Random iteration must fail, receive a correct patch, and
be accepted. Real weak agents can ignore a wrong skill and solve the task, or real
patchers can produce a defensible but incorrect candidate that replay rejects.

Changing this criterion is scientifically meaningful and requires an explicit decision.
The recommended option is:

- keep the earlier individual Task 15 live evidence as the required proof that accepted
  revisions carry forward;
- make the final two-model gate validate every observed transition against its inputs,
  checker results, replay results, and resulting skill hash;
- require at least one real behavioral outcome per model, but do not demand a lucky
  accepted patch from every stochastic run; and
- never retry a model case merely to obtain `accepted`.

An alternative is to redesign the bounded fixture so acceptance is deterministic while
still using real S0 generation, weak-agent execution, patching, and replay. Do not choose
between these silently.

## P1: complete before declaring Task 16 done

### P1-1: exception-safe task-container lifecycle

Normal `execute_checks` cleanup works, but there is no single `try/finally` owner covering
task start, agent execution, verifier/check setup, durable results, and removal. An
exception before `execute_checks` can leave a detached `skillrace-run-*` container.

Add direct cleanup on every exit path while preserving host evidence. Do not add a
recovery framework or background janitor.

### P1-2: failed gate receipt loses the Part II evidence link

The dual gate calculates the new Part II directory only after its child pytest succeeds.
When the child fails, `gate.json` records `part2_evidence: null` even though a complete
campaign evidence directory exists.

Resolve and record the new directory before raising the child failure. Keep the child
stdout/stderr and sanitized failure summary.

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
- `skillrace_next/README.md` and `skillrace_next/docs/`;
- `tests_next/unit/test_cli.py`;
- `tests_next/unit/test_documented_cli.py`;
- `tests_next/live/test_part1_tiny_live.py`;
- `tests_next/live/test_exact_replay_live.py`; and
- `tests_next/live/test_dual_model_gate_live.py`.

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
