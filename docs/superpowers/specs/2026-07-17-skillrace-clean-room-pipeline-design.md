# SkillRACE Clean-Room Pipeline Rebuild Design

**Date:** 2026-07-17

**Status:** Approved design for implementation handoff

**Primary semantic source:** `/home/jim/skillrace/updated_pipeline.md`

## 1. Purpose

Rebuild SkillRACE in a new, independent Python package that implements the two
experiments in `updated_pipeline.md` directly. The rebuild must copy only small,
understood mechanisms from the existing implementation. It must not preserve the old
orchestration, schemas, compatibility layers, or one-shot RQ3 design.

The new implementation has two scientific goals:

1. **Part I — existing-skill bug discovery and independent repair.** Every discovery
   execution tests the same immutable original skill `S0`. Each distinct confirmed bug
   may be patched and replayed independently, but no patch enters later discovery runs.
2. **Part II — generated-skill iterative improvement.** Each method receives an identical
   copy of one generated `S0`, then carries accepted revisions forward as
   `S0 -> S1 -> ... -> Sn` before a held-out evaluation.

This document also fixes four decisions made after `updated_pipeline.md` was written:

- Only the **verifier** uses Codex.
- Every other model-driven role, including the weak task agent and patcher, uses **Pi
  with the same cheap Yunwu model within a model track**.
- Codex inspects the host-side, read-only evidence bundle. It does not use Docker or
  `docker exec` while authoring checks.
- The finished checks are executed in the task container with `docker exec`; the
  deterministic runner, not Codex, records the authoritative verdicts.

## 2. Non-negotiable simplicity and anti-overthinking rules

**This pipeline is intentionally straightforward. It is a pair of sequential research
loops, not a platform, framework, distributed system, or general benchmark product.** The
implementation should look boring: ordinary functions called in order, a few dataclasses,
JSON files, subprocesses, and explicit `if` statements. That is the desired design.

The complete control flow is:

```text
Part I
for each method and budget slot:
    propose test -> validate -> run S0 -> author/execute checks -> update method state
group failures
for each confirmed group:
    patch a fresh copy of S0 -> exact replay -> record accepted/rejected repair

Part II
S = identical copy of generated S0
for each development iteration:
    choose test -> run S -> execute predefined checks -> update method state
    if failed:
        candidate = patch(S)
        if exact replay passes and retained tests do not regress:
            S = candidate
evaluate final S on held-out tests
```

An implementation that directly expresses those two loops is preferable to a more
abstract implementation, even if a little code is duplicated. Do not spend time finding
a more sophisticated architecture for a control flow that is already settled.

The implementation agent must follow these rules even when a more general abstraction
looks attractive:

1. Build only under `skillrace_next/` and `tests_next/` until cutover.
2. `skillrace_next` must never import the old `skillrace` package.
3. Use Python standard-library dataclasses and JSON. Do not introduce a database,
   Pydantic, an ORM, a workflow engine, a plugin framework, or an event-sourcing layer.
4. Use one sequential loop inside a method/campaign cell. Parallelize only independent
   skill x method x replicate cells after the sequential implementation is correct.
5. Use one Pi patcher implementation for all methods. Do not add direct-versus-Pi repair
   backends or method-specific patcher implementations.
6. Use one verifier path. Do not retain pre-run, post-hoc, path-only, Bash-only, Python-
   only, legacy, or RQ3-specific checker modes in the active package.
7. Use one replay function and one patch acceptance function for both parts.
8. Keep JSON record types small. Add a field only when a named pipeline stage consumes
   it or a reported metric requires it.
9. Do not add schema migration code. The clean-room package has no historical schemas to
   support.
10. Do not add automatic recovery state machines. A stage writes one terminal receipt;
    interrupted paid calls use the existing durable provider journal mechanism that is
    selectively ported.
11. Allow at most one bounded retry for a malformed model response or transient provider
    error. Record both attempts. Never retry an experimental agent execution silently.
12. Do not create generic base classes when a tagged dataclass and three explicit
    functions are enough.
13. Prefer modules below roughly 400 lines. Split a file only when it contains two clear
    responsibilities; do not split merely to satisfy a line count.
14. No speculative features, dashboards, distributed scheduling, web UI, cache service,
    generalized benchmark SDK, or compatibility facade.
15. A passing mocked/offline test is not sufficient for any Yunwu, Pi, Docker, verifier,
    patch, or replay boundary. The live gates in Section 16 are mandatory.
16. Do not re-plan, re-litigate, or compare alternative architectures after beginning
    implementation. This document already contains those decisions. Reason about whether
    the code matches the contract, not about replacing the contract.
17. Do not create an abstraction for one call site or for a hypothetical future method,
    provider, model, storage backend, scheduler, or experiment.
18. Small local duplication is acceptable when removing it would hide the order of the
    pipeline or require configuration indirection.
19. Implement one thin working vertical slice before adding exhaustive validation,
    reporting, convenience commands, or additional fixtures.
20. Do not generate empty modules, interfaces, factories, registries, managers, services,
    adapters, or repositories in anticipation of later phases.
21. Handle the required and observed failure modes in Section 15. Do not build machinery
    for imagined crashes, distributed workers, process migration, or arbitrary resumption.
22. Do not write another architecture document, migration proposal, or meta-plan during
    implementation. Record only short phase evidence and genuine decisions forced by an
    ambiguity in this contract.

When uncertain, implement the smallest behavior explicitly required by this document and
record the uncertainty. Do not invent a general solution.

## 3. Clean-room package boundary

Use this initial structure:

```text
skillrace_next/
├── __init__.py
├── __main__.py
├── cli.py
├── config.py
├── records.py
├── storage.py
├── runtime/
│   ├── pi.py
│   ├── docker.py
│   └── artifacts.py
├── verification/
│   ├── codex.py
│   ├── executor.py
│   └── GUIDE.md
├── methods/
│   ├── random.py
│   ├── verigrey.py
│   └── skillrace.py
├── pipeline/
│   ├── stages.py
│   ├── part1.py
│   └── part2.py
└── analysis/
    ├── part1.py
    └── part2.py

tests_next/
├── unit/
├── integration/
└── live/
```

This is a target, not a demand to create empty files. A file should be created only when
the corresponding behavior is implemented.

During the rebuild, the old package is read-only reference material. After the cutover
criteria pass, `skillrace_next` becomes the canonical `skillrace` package and the old
implementation moves under `legacy/`. Historical readers may live under `legacy/`, but
the canonical package must not import them.

Add a simple dependency guard test that parses imports under `skillrace_next/` and fails
on `import skillrace` or any import beginning with `skillrace.`. Do not build a more
elaborate boundary checker.

## 4. Model and process roles

Within one model track, freeze exactly one cheap Yunwu model and use it for every
non-verifier model role:

| Role | Mechanism | Model |
|---|---|---|
| Test proposer/generator | Pi | Track's cheap model |
| Weak task agent | Pi in task container | Track's cheap model |
| Trace segmenter | Pi | Track's cheap model |
| SkillRACE tree alignment, when needed | Pi | Track's cheap model |
| Skill generator for Part II `S0` | Pi | Track's cheap model |
| Skill patcher | Pi | The same track model |
| Verifier/check author | Local Codex | Strong Codex model |
| Check execution and verdict | Python + `docker exec` | No model |
| Patch acceptance and metrics | Python | No model |

Development uses `deepseek-v3.2` as the primary Yunwu model after a successful direct and
Pi preflight. The final development gate repeats the end-to-end slices with both
`deepseek-v3.2` and `glm-4.7`. A failed preflight blocks that model's live gate; it does
not become an experimental failure.

All three methods use identical model IDs, Pi versions, prompts for shared roles, task
budgets, Docker policy, verifier configuration, patcher code, and replay rules. Only the
test-selection state and method-specific patch evidence differ.

## 5. Minimal durable records

`records.py` defines only the following dataclasses and their JSON serialization:

1. `ExperimentConfig`
2. `SkillVersion`
3. `TestCase`
4. `RunRecord`
5. `CheckBundle`
6. `CheckResults`
7. `PatchAttempt`
8. `ImprovementStep`

Method state is stored by the method module as plain JSON-compatible data. It is not a
global schema hierarchy.

### 5.1 ExperimentConfig

One experiment config replaces the old protocol/schedule fragmentation. It contains:

- experiment ID and part (`part1` or `part2`);
- methods and replicate count;
- Yunwu provider, model ID, Pi version, and role budgets;
- Codex verifier command/model/reasoning setting;
- Docker image, resource limits, network policy, and timeouts;
- input suite/scenario paths;
- discovery or improvement iteration budget;
- live/development flag;
- output root;
- held-out repetition count for Part II.

Write the normalized config and SHA-256 hash at experiment start. No stage may read a
different config file after the run begins.

### 5.2 SkillVersion

Contains only `skill_id`, `version_id`, `parent_version_id`, directory path, tree hash,
creation role, model ID, and creation/patch receipt path. `S0` has no parent.

### 5.3 TestCase

Contains `test_id`, prompt path/hash, environment directory/hash, NL-check path/hash,
origin method, proposal receipt, validation status/diagnostic, and container image ID.

### 5.4 RunRecord

Contains the test and skill identities, method, model, budget, container/image identity,
start/end times, termination status, host artifact path/hash, trace and tool-log paths,
stdout/stderr paths, provider receipt paths, and cost totals. The permitted termination
statuses are `completed`, `agent_timeout`, `container_error`, and `provider_error`.

### 5.5 CheckBundle and CheckResults

These bind the verifier input hashes to the exact scripts and manifest, then bind the
Docker execution results to that bundle. Their concrete layout is defined in Section 8.

### 5.6 PatchAttempt

Contains input skill hash, evidence-bundle hash, method, same-track Pi model, Pi trace and
cost receipt, candidate skill hash, patch status, replay path, and acceptance status.

### 5.7 ImprovementStep

Part II only. Contains iteration, input skill version, test/run/check IDs, patch attempt
ID when present, accepted/rejected decision, resulting skill version, and regression-set
results.

Every record has one schema string ending in `/1`. Do not implement schema registries or
migrations.

## 6. Shared stage functions

`pipeline/stages.py` exposes the concrete operations shared by both parts:

```text
propose_test(method, state, skill, config) -> TestCase
validate_test(test, config) -> TestCase
run_agent(skill, test, config) -> RunRecord
author_checks(run, skill, test, config) -> CheckBundle       # Part I only
execute_checks(run, bundle, config) -> CheckResults
update_method_state(method, state, run, results) -> state
build_patch_evidence(method, state, run, results) -> directory
patch_skill(skill, evidence, config) -> PatchAttempt
replay(skill, test, check_bundle, config) -> replay result
accept_patch(before_results, replay_results, regressions) -> bool
```

Part II passes its predefined check bundle directly to `execute_checks`; it does not call
Codex to regenerate benchmark checks.

Each stage either returns a complete typed result or raises one stage-specific error. The
caller writes one terminal receipt. Do not create a general DAG executor.

## 7. Test proposal and environment validation

For each requested execution slot:

1. The method proposes/selects a test according to Section 11.
2. Validate all referenced files, prompt and NL-check structure, Docker build, basic
   container startup, and benchmark-specific sanity conditions before spending an agent
   call.
3. Permit one replacement proposal after an invalid proposal. If the replacement is also
   invalid, record a missed slot and continue. Invalid proposals do not become agent
   failures or discovered bugs.
4. Build or resolve the immutable validated image once. Reuse that image to start a fresh
   task container; do not rebuild the same Dockerfile again in `run_agent`.

The execution budget counts weak-agent executions, not proposal attempts. Proposal cost
and invalid-proposal counts are reported separately.

## 8. Codex verifier contract for Part I

### 8.1 Host-side verifier workspace

After the weak agent ends, all verifier inputs are available locally under one logical
workspace:

```text
<run>/verifier_workspace/
├── GUIDE.md
├── input/
│   ├── skill/
│   ├── prompt.txt
│   ├── environment/
│   ├── artifact/
│   ├── trace.jsonl
│   ├── tool_outputs.jsonl
│   ├── run.json
│   └── nl_checks.json
└── output/
    ├── check_manifest.json
    └── checks/
```

The implementation may expose the existing host directories as a read-only view instead
of copying large trees. Codex must perceive the layout above and only `output/` may be
writable. Hash the skill and artifact before and after Codex. Any mutation invalidates
the verifier run.

Codex does not receive Docker access. It may inspect files and run local read-only
commands for understanding, but local executions are not verdicts. Only the later Docker
executions are authoritative.

### 8.2 Required verifier guide

`verification/GUIDE.md` must state, prominently and unambiguously:

> Your sole task is to determine whether the immutable final artifact satisfies each
> supplied natural-language check. You may inspect the skill, prompt, environment,
> artifact, trace, and tool outputs. You must not modify, repair, complete, reformat, or
> otherwise improve the artifact or skill. Write only executable checks and verification
> metadata into `output/`. Judge the artifact as it exists. Do not claim a pass or failure
> from a local exploratory command; the orchestrator will execute your declared checks in
> the task container. If a property cannot be checked defensibly, mark it uncovered with
> a reason rather than guessing.

The guide also explains every input path, the output schema, exit-code meanings, scratch
directory, and prohibition on network access and artifact writes.

### 8.3 check_manifest.json

Codex writes one manifest plus one or more Bash/Python scripts:

```json
{
  "schema": "skillrace-check-bundle/1",
  "run_id": "run-id",
  "artifact_hash": "sha256",
  "checks": [
    {
      "check_id": "P1-C1",
      "property_id": "P1",
      "script": "checks/P1-C1.py",
      "argv": ["python3", "/tmp/skillrace-checks/checks/P1-C1.py", "/workspace"],
      "timeout_seconds": 60,
      "purpose": "Concrete behavior this script measures",
      "pass_condition": "Observable condition corresponding to exit 0",
      "failure_condition": "Observable condition corresponding to exit 1",
      "root_cause_category": "format_contract"
    }
  ],
  "uncovered": [
    {
      "property_id": "P2",
      "reason": "Why no defensible executable check can be authored"
    }
  ]
}
```

One NL property may have multiple checks. Every property must appear in either `checks`
or `uncovered`. `root_cause_category` uses a small fixed set:

- `instruction_missing`
- `instruction_ambiguous`
- `wrong_workflow`
- `tool_misuse`
- `validation_missing`
- `format_contract`
- `environment_assumption`
- `other`

The category is a method-blind grouping hint, not a claim that the verifier repaired or
fully diagnosed the skill.

Check scripts communicate through exit status:

- `0`: pass;
- `1`: fail;
- `2`: inconclusive.

Each script prints one JSON object containing a concise `diagnostic`, observed values,
and relevant artifact-relative evidence paths. A malformed report, undeclared script,
unexpected exit status, timeout, or execution infrastructure error becomes
`inconclusive`, never `fail`.

Validate only manifest shape, relative script containment, argv form, timeout bound, and
property coverage. Do not build a custom shell-language security analyzer. OS permissions
and the isolated container provide the enforcement boundary.

If Codex produces an invalid bundle, make one correction call containing the validation
errors. If the second bundle is invalid, record all properties as inconclusive and stop.

### 8.4 Docker execution and check_results.json

The task container remains alive after the weak agent process ends. The orchestrator:

1. freezes the host-mounted artifact and makes it non-writable for the restricted checker
   UID;
2. hashes the frozen artifact, including file content and relative paths;
3. copies the validated check bundle to `/tmp/skillrace-checks`;
4. invokes each manifest argv with `docker exec --user <checker-uid>`;
5. gives checks a writable `/tmp/skillrace-check-work` scratch directory;
6. captures exit status, stdout, stderr, duration, and timeout;
7. re-hashes the artifact after all checks;
8. invalidates the check run if the artifact changed; and
9. removes the container in a `finally` block after evidence is durable.

Codex never receives the container ID or Docker socket. The orchestrator uses argv arrays,
not interpolated host shell strings.

The runner writes the authoritative result:

```json
{
  "schema": "skillrace-check-results/1",
  "run_id": "run-id",
  "check_bundle_hash": "sha256",
  "artifact_hash_before": "sha256",
  "artifact_hash_after": "sha256",
  "artifact_unchanged": true,
  "results": [
    {
      "check_id": "P1-C1",
      "property_id": "P1",
      "status": "pass",
      "exit_code": 0,
      "duration_seconds": 0.42,
      "diagnostic": "Observed value matched the required condition",
      "stdout_path": "outputs/P1-C1.stdout",
      "stderr_path": "outputs/P1-C1.stderr",
      "evidence_paths": ["result.json"]
    }
  ]
}
```

The verifier's manifest, scripts, Codex tool trace/receipt, and authoritative results are
all retained. The patcher receives this complete evidence. The model's prose alone is
never an authoritative verdict.

## 9. Container and artifact lifecycle

Use one owner for the complete attempt lifecycle: validate/start, weak-agent exec,
artifact freeze, verification/check execution, and cleanup. Do not split cleanup ownership
between multiple commands or detached timebomb processes.

Start the task container with an inert supervisor process, then run Pi as a child. If Pi
times out, terminate the Pi child while keeping the container available for artifact
capture and checks. The host-mounted artifact is the durable final artifact, including
partial work after a timeout.

Preserve:

- complete artifact tree and hash;
- Pi trace, reasoning blocks, tool calls, outputs, and timeouts;
- stdout/stderr;
- immutable environment/image identity;
- provider operation receipts and usage; and
- container/check execution diagnostics.

Never rely on a temporary container snapshot as the only artifact. Never delete the
container until all host-side evidence is durable. Cleanup failure is infrastructure
metadata, not an experimental failure.

## 10. Part I — existing-skill discovery and independent repair

For each skill and model track:

1. Freeze `S0`, properties, config, Pi version, image policy, Codex verifier config, and
   budgets.
2. Create independent Random, VeriGrey, and SkillRACE method states.
3. For every method and discovery slot:
   1. propose/select and validate a test;
   2. start a clean container with `S0`;
   3. run the weak Pi agent;
   4. freeze the final artifact and trace;
   5. have Codex author checks locally;
   6. execute the checks with `docker exec`;
   7. persist results; and
   8. update only that method's exploration state.
4. Collect failure candidates. Group before patching using
   `(property_group, failing-check signature, root-cause category)`.
5. Select one representative per group and replay it once with unchanged `S0` and the
   same checks. Report raw candidates separately; only reproduced groups are confirmed
   distinct bugs.
6. For each confirmed representative, build the common plus method-specific patch
   evidence and run the shared Pi patcher on a fresh copy of `S0`.
7. Replay the patched skill on the same test/environment/model/budget/check bundle.
8. Accept a repair only if at least one previously failed check passes and every
   previously passing check still passes. Inconclusive does not count as repaired.
9. Discard the independent patched copy after recording it. Discovery always continues
   against `S0`.

Primary Part I outputs are:

- raw failure candidates;
- confirmed distinct bugs in `S0`;
- confirmed distinct bugs successfully repaired;
- repair success rate over confirmed bugs;
- per-method agent, verifier, patch, replay, time, token, and provider-credit costs; and
- inconclusive and infrastructure-failure counts reported separately.

Do not use a metric that requires a bug to be repaired before it counts as discovered.

## 11. Method state and permitted evidence

### 11.1 Random

Random independently proposes a valid test from the property/task space. It retains no
adaptive state. Its patcher receives only common evidence.

### 11.2 VeriGrey

VeriGrey records normalized tool sequences and coverage counts. Normalization must keep
tool name and stable argument shape, not full arbitrary argument contents. Its next test
targets a novel or under-covered tool transition. Do not implement reservation, epoch, or
parallel-completion machinery.

Its patcher receives the common evidence plus the run's normalized tool sequence, novelty
delta, and relevant coverage counts.

### 11.3 SkillRACE

SkillRACE performs at most:

1. one Pi segmentation call per run, producing ordered episodes with `purpose`, `outcome`,
   and `reason_for_next`; and
2. one batched Pi tree-alignment call when deterministic placement is ambiguous.

The behavior tree stores reasoning-labelled edges, branch reach status, member run and
episode IDs, and associated check failures/diagnostics. It chooses an unreached or
reasoning-unexplored branch for the next test. Do not make one model call per node or
maintain parallel branch reservations.

Its patcher receives common evidence plus the run's episodes, relevant tree path,
targeted branch, reach state, and failure associations.

### 11.4 Common patch evidence

All methods receive exactly the same common evidence:

- current skill;
- test prompt and environment description;
- immutable final artifact;
- trace and tool outputs;
- NL checks;
- generated/predefined check scripts;
- authoritative check results and diagnostics; and
- termination and budget metadata.

The patcher may edit only `SKILL.md` inside the copied skill package. Every other skill
file and all artifact, test, environment, check-script, and result files remain unchanged.

## 12. Shared Pi patcher and exact replay

Use one guided Pi patcher for all methods and both experiment parts. Within a model track,
it uses the same cheap model as the weak task agent. Its prompt and tool policy are
identical across methods; the evidence bundle is the only permitted difference.

The patcher must:

1. read the skill and evidence before editing;
2. explain the failure briefly in its saved trace;
3. make a bounded edit to the copied skill;
4. avoid executing the benchmark or changing any evidence; and
5. terminate after the configured edit/turn limit.

Exact replay means a new clean task container, the same initial environment, prompt,
weak model, Pi version, budget, and exact check scripts. It does not mean reusing the
dirty final container.

`accept_patch` is deterministic:

- at least one prior `fail` must become `pass`;
- every prior `pass` must remain `pass`;
- a prior `pass` becoming `fail` or `inconclusive` rejects the patch;
- a prior `fail` becoming `inconclusive` is not a repair; and
- infrastructure failure does not accept or reject scientifically; it leaves the attempt
  unresolved and visible.

## 13. Part II — generated-skill iterative improvement

For each scenario:

1. Freeze the development/held-out split and predefined executable checks.
2. Generate one zero-shot `S0` with Pi and the track's cheap model. Preserve prompt,
   trace, usage, and hash.
3. Give every method a byte-identical copy of `S0` and its own exploration state.
4. For each method and development iteration:
   1. select/generate one development test using the current `Si` and method state;
   2. run `Si` with the weak Pi agent in a clean container;
   3. execute the test's predefined checks with `docker exec`;
   4. update the method state;
   5. if nothing fails, retain `Si` and continue;
   6. if checks fail, run the shared Pi patcher with common and method-specific evidence;
   7. replay the candidate on the exact failing test and checks;
   8. replay every previously retained development regression test; and
   9. accept the candidate as `Si+1` only when the current failure improves and no
      retained test regresses. Otherwise retain `Si`.
5. Evaluate the final skill from each method on held-out tests in fresh containers. The
   patcher and method state cannot access held-out paths before this phase.
6. Repeat held-out executions according to the frozen repetition count when the weak
   agent is stochastic.

Part II uses predefined checks. Codex check generation is a Part I operation and must not
be inserted into held-out evaluation.

Report held-out per-test pass rates, all-tests-pass rate, scenario mean/median, pairwise
wins, regressions relative to `S0`, accepted/rejected revisions, and full costs.

Do not implement equal-byte feedback envelopes, one revision per whole campaign, or four
conditions consisting of base plus one revision. The scientific object is each method's
iteratively evolved final skill.

## 14. CLI and persistence

Provide one CLI:

```text
python -m skillrace_next live-smoke --config <config.json>
python -m skillrace_next part1 --config <config.json>
python -m skillrace_next part2 --config <config.json>
python -m skillrace_next analyze --run <run-directory>
```

Do not expose internal stages as public CLIs unless a concrete debugging need arises.

Use ordinary directories and atomic JSON writes:

```text
out/<experiment-id>/
├── config.json
├── config.sha256
├── cells/<skill-or-scenario>/<model>/<method>/<replicate>/
│   ├── state.json
│   ├── runs/<run-id>/
│   ├── patches/<patch-id>/
│   └── summary.json
└── analysis/
```

A run directory contains immutable inputs, artifact, trace, verification/check evidence,
and its terminal receipt. Do not write nine lifecycle files for one attempt. Atomic
temporary-file-plus-rename is enough for completed JSON. Durable paid-call journaling is
the only finer-grained recovery mechanism.

## 15. Failure and retry semantics

Keep categories disjoint:

- `pass`, `fail`, `inconclusive`: property-check outcomes;
- `agent_timeout`: a completed experimental execution with a partial artifact;
- `provider_error`, `container_error`, `checker_error`: infrastructure outcomes;
- `invalid_test`: pre-agent validation outcome; and
- `patch_timeout`/`patch_invalid`: repair outcomes.

Never convert a provider, Docker, malformed-checker, or timeout condition into a property
failure. Preserve partial artifacts and diagnostics whenever possible.

Permit one retry only for:

- a direct/Pi preflight transient provider response;
- malformed test-proposal JSON;
- malformed Codex check bundle; or
- malformed patcher edit response when no file was changed.

Do not retry weak-agent experiment executions automatically. Reproduction and exact
replay are explicit scientific stages with new IDs and costs.

## 16. Mandatory live development gates

Offline tests remain necessary but cannot complete a model/runtime milestone by
themselves. Use development-only fixtures, never headline/held-out data, for these gates.

### Individual live contract rule

Every self-contained component must pass its own online contract test immediately after
it is implemented and before a downstream component depends on it. A later whole-pipeline
run does not substitute for these component tests. Each test gets a named command, its own
saved evidence directory under `out/live-contracts/<component>/<run-id>/`, and explicit
semantic assertions in addition to schema validation.

Use the real online service belonging to the component:

| Component | Required individual online test |
|---|---|
| Yunwu transport and Pi adapter | One direct Yunwu call and one real Pi tool call |
| Test proposer/generator | One Pi/Yunwu proposal that passes deterministic test validation |
| Weak task agent/runner | One Pi/Yunwu Docker task producing a preserved artifact and trace |
| Part II `S0` skill generator | One Pi/Yunwu generation producing a valid isolated skill |
| Episode creator/segmenter | One Pi/Yunwu segmentation of a real saved agent trace; verify ordered, source-grounded episodes |
| SkillRACE tree merger/alignment | One Pi/Yunwu merge/alignment using real episodes; verify nodes, reasoning-labelled edges, membership, and reach state |
| VeriGrey state/proposal path | One real Yunwu-backed proposal using saved tool-sequence novelty evidence |
| Pi patcher | One Pi/Yunwu patch from a defensible saved failure; verify only `SKILL.md` changes |
| Codex verifier/check author | One real Codex invocation over an artifact produced by a real Yunwu run; verify inputs remain unchanged and a valid check bundle is written |
| Docker check executor | Execute that real Codex-authored bundle with `docker exec`; verify authoritative results and artifact immutability |
| Exact replay/acceptance | One real Yunwu replay of the patched skill using the exact saved checks; verify the deterministic decision |

The checker is intentionally **not** tested by replacing Codex with Yunwu. Its individual
test uses real Codex for check authoring, a real artifact produced by the Yunwu task agent,
and real Docker execution. Pure deterministic helpers such as hashing, JSON parsing,
deduplication, and acceptance predicates receive focused offline tests; making an
irrelevant paid model call does not improve their validation.

Each model-facing component contract test is bounded to one normal model invocation plus
the single retry already allowed in Section 15. The first successful live output from the
episode creator, tree merger, test proposer, skill generator, patcher, and verifier must
also receive a brief human semantic review; syntactically valid nonsense is not a pass.

The component's live test command and saved evidence path must be recorded in the phase
commit message or its accompanying phase note. Do not defer individual online testing
until the complete Part I or Part II loop exists.

### Gate A — provider and Pi boundary

- one minimal direct Yunwu call;
- one real Pi tool-use probe;
- verify exact model ID, structured tool use, trace, usage, and redacted operation receipt;
- primary model: `deepseek-v3.2` after preflight.

### Gate B — Docker task vertical slice

- start a real validated task container;
- run a small Pi task through Yunwu;
- preserve the host-mounted artifact, trace, timeout state, and cost;
- prove deterministic cleanup after evidence is durable.

### Gate C — Codex verifier vertical slice

- give Codex a real locally mounted skill/prompt/environment/artifact/trace/NL-check
  workspace;
- prove inputs are unchanged and only verifier output is written;
- execute the authored checks in the task container with `docker exec`;
- retain scripts and authoritative JSON results;
- manually review this first live bundle for semantic validity.

### Gate D — Pi patch and replay vertical slice

- use a manually defensible real failure;
- run the same-model Pi patcher;
- confirm it changes only the copied skill;
- perform a new real weak-agent replay with the exact checks; and
- exercise deterministic acceptance/rejection.

### Gate E — tiny Part I campaign

- one development skill;
- one agent execution per method;
- real Random, VeriGrey, and SkillRACE state updates;
- group before patching;
- repair at most one confirmed representative per method.

### Gate F — tiny Part II campaign

- one development scenario;
- at least two sequential improvement iterations per method;
- prove accepted revisions carry forward and rejected revisions do not;
- run a small held-out evaluation unavailable during improvement.

### Gate G — dual-model final development gate

Repeat the tiny Part I and Part II end-to-end slices with both `deepseek-v3.2` and
`glm-4.7`, after fresh direct and Pi preflights. Do not silently substitute models within
a run.

Every live gate records request/operation IDs, exact model and Pi versions, traces, token
usage, wall time, artifact/config hashes, and sanitized provider errors. Record Yunwu
provider credits only from dated or provider-reported rate evidence; otherwise mark the
usage `unpriced` rather than inventing a conversion. Require an explicit `--live` flag. A
live gate uses the bounded fixture described below and must never trigger a full campaign
automatically.

Live development bounds are:

- Gate A: one direct request and one Pi probe per model, plus at most one transient-error
  retry;
- Gate B: one task, at most four Pi tool turns, and a 180-second wall timeout;
- Gate C: at most two NL checks, one Codex authoring call, and one correction call only if
  the bundle is structurally invalid;
- Gate D: one patch attempt capped at six Pi turns and 300 seconds, followed by one
  replay;
- Gate E: exactly one agent execution per method and at most one representative repair
  per method;
- Gate F: exactly two improvement iterations per method and one held-out execution per
  final skill; and
- Gate G: repeat those same bounded Gate E and Gate F fixtures once per required model.

If Yunwu returns a persistent 429/5xx or the model lacks structured Pi tool use, mark the
gate blocked with evidence and stop spending. Do not modify scientific logic to make a
provider outage look successful.

## 17. Selective port whitelist

Copy behavior, not files or architecture. Candidates for small reviewed extraction are:

- Yunwu request journaling, redaction, usage, and cost calculations from `closeai.py` and
  `provider_evidence.py`;
- the minimum Pi invocation/tool-policy behavior from `pi_patcher.py`, `gen_agent.py`,
  and `segment_agent.py`;
- Docker command, timeout, trace, and artifact-capture primitives from `run_case.py`;
- atomic JSON and hashing primitives from `io_utils.py` and `input_identity.py`;
- deterministic sanity checks from `sanity.py` and scenario isolation rules from
  `scenario_contract.py`/`rq3_isolation.py`;
- normalized tool-sequence logic from `greybox.py`; and
- episode/tree data behavior from `segment.py` and `tree.py`.

For each port:

1. identify the exact function/behavior and its required inputs/outputs;
2. write a focused `tests_next` test;
3. copy or rewrite only that behavior into the new package;
4. remove old schema and orchestration dependencies; and
5. pass the relevant offline and live gate before depending on it.

Do not bulk-copy a module merely because it already works. In particular, `closeai.py`,
`run_case.py`, `greybox.py`, and `tree.py` must be reduced to the small behavior required
by the new contract.

## 18. Explicit do-not-port list

The active rebuild must not copy or import these architectural paths:

- `campaign_engine.py`, `loop.py`, `experiment_driver.py`, `parallel_campaign.py`;
- `adaptive_artifacts.py`, reservation/epoch state, fold recovery, and multi-file attempt
  lifecycle protocols;
- `compile_checks.py`, `check_properties.py`, and their pre-run/path-only/legacy modes;
- `direct_patcher.py`, `patch_only.py`, `patch_confirmation.py`, and the existing combined
  `repair_validation.py` orchestration;
- `feedback.py`, `revise_skill.py`, equal-byte feedback envelopes, and one-shot revision
  design;
- the `rq3.py`, `rq3_campaign.py`, `rq3_confirmation.py`, `rq3_driver.py`, and
  `rq3_pipeline.py` orchestration chain;
- old aggregate compatibility schemas and historical active-path readers; and
- draft protocol/schedule proliferation.

These files remain available only as evidence of old behavior and sources for narrowly
identified primitives.

## 19. Testing strategy

### Unit tests

Test deterministic contracts: config normalization, hashes, record round-trips, check
manifest validation, exit-code mapping, acceptance rules, deduplication keys, method state
updates, and artifact mutation detection.

### Docker integration tests

Use local deterministic fixtures to test container lifecycle, artifact preservation after
timeout, restricted check execution, scratch writes, stdout/stderr capture, cleanup, and
held-out path isolation.

### Live tests

Place paid tests under `tests_next/live/` and mark them `live`. They require `--live` and
the `yunwu_key` environment variable. Mocks may test errors but never replace Gates A-G.

### Documentation/CLI smoke

Every documented command must be run by a smoke test against `--help` or a tiny offline
fixture. This prevents the current README/CLI drift from recurring.

Do not copy the old 21,000-line test suite. Port only tests that assert a retained
scientific or runtime requirement. Historical schema and compatibility tests stay with
legacy code.

## 20. Build order and checkpoints

Implement in this order. Do not start a later phase until the named gate passes.
Each phase is an implementation checkpoint, not an invitation to redesign or broaden the
phase. Build the direct happy path first, then add only the error handling named in this
document.

1. **Boundary skeleton:** package, config, records, storage, import guard, and tiny CLI.
2. **Runtime vertical slice:** selective Yunwu/Pi/Docker/artifact port. Pass Gates A-B.
3. **Verifier vertical slice:** Codex workspace, manifest validation, Docker executor,
   immutable artifact enforcement. Pass Gate C.
4. **Shared patch/replay:** one Pi patcher, evidence bundle, exact replay, deterministic
   acceptance. Pass Gate D.
5. **Method states:** Random, then VeriGrey, then SkillRACE, each with focused state tests.
6. **Part I loop and analysis:** immutable `S0`, group-before-patch, separate discovery and
   repair metrics. Pass Gate E.
7. **Part II loop and analysis:** one generated `S0`, sequential accepted revisions,
   regression set, held-out isolation. Pass Gate F.
8. **Operational polish:** documented CLI, config examples, crash-visible receipts, and
   artifact smoke.
9. **Dual-model validation:** pass Gate G.
10. **Cutover:** review the diff and metrics, rename the new package to canonical
    `skillrace`, move old code/readers under `legacy/`, and update only canonical docs.

Each phase must end with:

- a small reviewable diff;
- focused offline tests;
- a separate live contract test for every model-facing component completed in that phase;
- the required real online run when the boundary is model/runtime-facing;
- saved evidence paths and costs; and
- a short note stating what was deliberately not generalized.

## 21. Cutover definition of done

The clean-room rebuild is ready to replace the old package only when:

1. no `skillrace_next` module imports old `skillrace` code;
2. all new unit and Docker integration tests pass;
3. every row in the individual live-contract matrix and Gates A-G have fresh saved
   evidence;
4. Part I proves all three methods run against immutable `S0`, group before patching, use
   the shared Pi patcher, and report discovery separately from repair;
5. Part II proves accepted skills evolve across at least two iterations and held-out data
   is inaccessible before final evaluation;
6. Codex inputs and checked artifacts remain byte-identical before/after verification;
7. every check verdict comes from captured `docker exec` execution, not model prose;
8. exact replay uses the same check bundle in a fresh task container;
9. the primary and final dual-model live gates have complete cost/provider receipts;
10. all canonical documented commands pass their smoke tests; and
11. a code review confirms that none of the do-not-port mechanisms reappeared under new
    names.

Passing the old offline suite is not a cutover requirement for the new package. The old
suite remains the legacy package's responsibility.

## 22. Instructions for the implementation agent

Use this document as the contract. Implement it phase by phase, beginning with one thin
end-to-end vertical slice. Do not begin by writing another plan or conducting another
whole-repository architecture review. The old implementation has already been audited.
Inspect an old module only when extracting one specific behavior from the whitelist.

Do not redesign the experiment while coding. If running with a high-reasoning or Max
model, spend that reasoning on implementation correctness, evidence, and debugging—not on
inventing abstractions or reconsidering settled choices.

Before adding any abstraction, answer all three questions:

1. Which exact requirement in this document needs it?
2. Which two current call sites use it?
3. Why is a direct function or dataclass insufficient?

If any answer is missing, do not add the abstraction.

Additional instructions:

- Do not touch or clean unrelated dirty files.
- Do not refactor the old package as part of the rebuild.
- Do not add compatibility adapters to the new package.
- Do not batch many phases into one unreviewable change.
- Do not scaffold later phases before the current vertical slice works.
- Do not replace obvious loops and conditionals with generic dispatch or workflow code.
- Do not expand a task because nearby old code looks inconsistent or untidy.
- Do not mark a model/runtime phase complete from mocks.
- Do not run headline campaigns during development.
- Do not spend paid calls after a failed preflight or beyond the smallest bounded fixture.
- Do not weaken isolation, artifact immutability, or result semantics to make a test pass.
- If this document is ambiguous in a way that changes scientific behavior, stop and ask
  the user. For ordinary implementation details, choose the simplest local solution.

The intended final system is a small, explicit research pipeline. Its value comes from
fair experimental roles, preserved evidence, reproducible executable checks, and clear
accept/reject semantics—not from framework sophistication.
