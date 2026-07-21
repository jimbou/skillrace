# Pipeline and Component Reference

This document describes the implemented `skillrace_next` code. It does not propose a new
architecture. Remaining non-blocking operational notes are listed in
[Current status and known issues](CURRENT_STATUS.md).

The package is runnable in place. Skill and scenario directories are data inputs, not
Python packages: callers may pass `skills/...` and `scenarios/...` paths without copying
them into `skillrace_next/` and without renaming the new package to `skillrace`.

## Package map

| Path | Responsibility |
|---|---|
| `config.py` | Strict JSON config loading and config freezing |
| `records.py` | Eight frozen durable record dataclasses |
| `storage.py` | Canonical JSON, SHA-256 hashing, tree hashing, atomic JSON writes |
| `runtime/providers.py` | Supported provider/model pairs, aliases, Pi model catalog, cost estimates |
| `runtime/pi.py` | Direct provider preflight and one bounded Pi invocation primitive |
| `runtime/docker.py` | Start, execute in, copy to, and remove task containers |
| `runtime/artifacts.py` | Hash and make artifact trees read-only |
| `verification/codex.py` | Read-only Codex checker authoring and manifest validation |
| `verification/executor.py` | Authoritative checker execution with `docker exec` |
| `methods/random.py` | Independent property-based test proposal |
| `methods/verigrey.py` | Tool-sequence normalization, coverage state, novelty-guided proposal |
| `methods/skillrace.py` | Episode extraction, reasoning tree merge/alignment, branch proposal |
| `pipeline/stages.py` | Concrete generation, validation, task, patch, replay, and acceptance stages |
| `pipeline/part1.py` | Immutable-S0 discovery loop and grouping |
| `pipeline/part2.py` | Cumulative-Si improvement loop and held-out evaluation |
| `pipeline/campaigns.py` | Direct CLI composition of the concrete stages for the two loops |
| `analysis/part1.py` | Discovery and repair counts/costs |
| `analysis/part2.py` | Held-out rates, comparisons, regressions, revision counts, costs |

The loops accept ordinary callback functions for their concrete proposal, execution,
check, state-update, patch, replay, and evaluation operations. `pipeline/campaigns.py`
wires those callbacks directly for the public CLI. It uses ordinary functions and local
dictionaries rather than a registry, service, or workflow abstraction.

## Input and output boundary

The direct CLI composition keeps repository data in three roles:

- `skills/...` may supply immutable Part I S0 directories and provenance receipts;
- `scenarios/...` may supply Part I properties, the Part II public scenario, and strict
  held-out `TestCase` records plus their assets; and
- `config.output_root` receives all generated development tests, S0 copies, runs,
  artifacts, checker bundles, patches, replays, held-out results, and summaries.

Input directories are referenced, hashed, and validated. They are not moved into the
package and are not used as Python import sources. Generated method tests are written
under the method/iteration evidence directory. Held-out records are first opened only
after all methods finish development.

## Durable records

`records.py` defines exactly eight frozen records. Each serialized form has a literal
`/1` schema and rejects unknown or missing fields.

| Record | What it binds |
|---|---|
| `ExperimentConfig` | Experiment, models, budgets, Docker policy, paths, timeouts |
| `SkillVersion` | Skill/version identity, parent, directory hash, creator receipt |
| `TestCase` | Prompt, environment, NL checks, proposal, validation, image identity |
| `RunRecord` | Skill/test/model/container identity, artifact, trace, status, cost |
| `CheckBundle` | Run/artifact/input hashes to a Codex manifest and scripts |
| `CheckResults` | Bundle/artifact hashes to authoritative Docker results |
| `PatchAttempt` | Input/evidence/candidate hashes, Pi trace, patch and acceptance status |
| `ImprovementStep` | One Part II transition from an input skill to a resulting skill |

Paths are `Path` values in Python and strings in JSON. JSON objects are written with
sorted keys and compact separators. Tree hashes cover every regular file's relative path
and bytes in sorted order.

## Shared stages

### Test validation

`validate_test` checks that prompt, environment, NL-check, and proposal paths remain
inside either the configured external suite root or the current output root. The second
root is required because each Part II method creates its own development tests inside
its evidence directory. It verifies stored hashes, NL-check IDs, Dockerfile and sanity
receipt, then executes:

```text
docker build -q <environment-directory>
```

The returned image ID is stored on the validated `TestCase`. A validation failure returns
the test with `validation_status="invalid_test"` and a diagnostic; it does not create an
experimental agent failure. The proposer receives one replacement opportunity. If that
replacement is also invalid, the loop writes `missed-slot.json`, reports it separately,
and continues without spending a weak-agent execution.

### Weak task-agent execution

`run_agent`:

1. Requires a validated `TestCase` and its exact image ID.
2. Creates host-mounted `artifact/` and `runtime/` directories.
3. Writes a minimal Pi `models.json` for the configured provider/model.
4. Starts a constrained Docker container with the artifact writable, runtime evidence
   writable, skill read-only, and model catalog read-only.
5. Executes Pi through `docker exec` with the exact skill path `/skill/SKILL.md`.
6. Preserves stdout, stderr, trace, tool-result records, usage, and provider receipt.
7. Freezes and hashes the artifact, even when the task agent times out.
8. Writes `run.json` with `completed`, `agent_timeout`, `container_error`, or
   `provider_error`.

The task container intentionally remains alive for checker execution. That coupling is
also the source of a current cleanup defect when a later stage raises early.

### Base-skill generation

`generate_base_skill` gives the configured track model the public scenario and requires
one complete, unfenced `SKILL.md` with YAML front matter containing nonempty `name` and
`description` values. It writes:

```text
generated-s0/
├── base/SKILL.md
├── methods/<method>/SKILL.md
├── generation/
│   ├── prompt.txt
│   └── pi/...
├── generation.json
└── skill-version.json
```

The method copies must be byte-identical. `S0` has no parent.

### Patch evidence

`build_patch_evidence` takes explicit `SkillVersion`, `TestCase`, `RunRecord`,
`CheckBundle`, and `CheckResults` values. Before copying anything, it verifies all skill,
test, artifact, manifest, run, and result hashes and identities.

Every method receives the same common evidence: current skill, prompt, environment, NL
checks, artifact, trace, tool outputs, run receipt, checker manifest/scripts/receipt, and
authoritative results. Random adds no method state. VeriGrey adds the last novelty
observation. SkillRACE adds episodes, tree, and selected branch. The completed evidence
tree is made read-only and hashed.

### Patching

`patch_skill` copies the input skill and runs the same-track Pi model with only `read` and
`edit` tools. The patcher is required to:

- read `/skill/SKILL.md` and `/evidence/evidence.json`;
- save a brief explanation before editing;
- edit only `/skill/SKILL.md`;
- avoid benchmark execution and test-specific memorization; and
- stop after the bounded edit.

The deterministic validator rejects changes to any other skill file, unchanged/empty/NUL
skill output, wrong tool ordering, evidence mutation, model timeout, or other invalid Pi
result. Outcomes are `patched`, `patch_timeout`, or `patch_invalid`.

Codex never patches skills. It only authors checks.

### Exact replay and acceptance

`replay` starts a fresh weak-agent task execution with the candidate skill and the same
test. It copies the exact prior checker scripts and receipt, rebinding only the fresh run
ID and artifact hash. The scripts then execute in the fresh task container.

`accept_patch` is deterministic:

- at least one prior failing check must become `pass`;
- all prior passing checks must remain `pass`;
- all retained regression checks must pass;
- an infrastructure-inconclusive result returns `unresolved`;
- mismatched or duplicated checker IDs return `unresolved`;
- otherwise the result is `accepted` or `rejected`.

Replay distinguishes behavioral timeout from infrastructure failure. An `agent_timeout`
keeps the partial frozen artifact and passes it to the authoritative checker without a
weak-agent retry. Provider and container failures remain terminal infrastructure errors,
and their containers are removed after evidence is durable.

## Part I: immutable-S0 discovery

`run_part1` receives one `SkillVersion` named `s0` and executes the following direct
loop:

```text
for method in config.methods:
    state = {}
    for slot in iteration_budget:
        test = propose(method, state, S0)
        run = execute(method, S0, test)
        results = check(run, test)
        state = update_state(method, state, run, results)
        save state.json
        collect fail candidates and inconclusive outcomes

group all candidates
for each group:
    confirm the representative
    if confirmed, patch its independent S0 copy
save summary.json
```

The loop verifies that every discovery execution reports the configured model and the
exact immutable S0 hash. Discovery patches are not carried into later discovery runs.

Failure grouping uses the exact tuple:

```text
(property_group, failing_check_signature, root_cause_category)
```

Part I metrics are raw candidates, confirmed distinct bugs, confirmed repaired bugs,
repair success rate, inconclusive count, infrastructure-failure count, and agent/patch
costs.

The CLI composition constructs S0 from explicit `--s0-dir`, `--s0-receipt`, and
`--skill-id` arguments. It uses the supplied property list for proposal, real weak-agent
execution, real Codex checker authoring, Docker execution, method-specific state update,
exact confirmation replay, same-track Pi patching, and candidate replay.

## Part II: cumulative improvement

`run_part2` verifies the generated S0 hash, then gives every method an identical copied
`S0`. Each method has its own current skill, state, accepted-version counter, and retained
development tests.

For each iteration:

1. Generate/select a development test from the public scenario and current method state.
2. Run the current skill and require the configured track model/version identity.
3. Have Codex author a checker bundle for the immutable artifact and execute it through
   `docker exec`.
4. Update and save method state.
5. If nothing fails, record `retained` and add the test to the regression set.
6. If checks fail, patch a copy of the current skill.
7. If the patch is valid, replay the failing test and every retained test.
8. Call `accept_patch`.
9. On `accepted`, copy the candidate to the next `S1`, `S2`, … directory and carry it
   forward. Otherwise retain the current version.
10. Write one `improvement-step.json`.

After every method's development loop has finished, `load_heldout()` is called for the
first time. S0 and each method's final skill are evaluated on identical held-out
test/repetition cells. Hidden tests cannot influence generation, development test
creation, patching, or admission.

Part II reports:

- per-test pass rates;
- all-tests pass rate;
- scenario mean and median;
- pairwise method wins/ties;
- regressions relative to S0;
- accepted, rejected, and unresolved revision counts; and
- agent, patch, replay, and held-out cost totals.

The summary requires every method to have exactly the same held-out cells as the S0
baseline.

## Method-specific state

### Random

Random independently proposes one prompt and Dockerfile per execution from the current
skill and complete frozen property catalog. The pipeline, not the model, attaches that
catalog unchanged. Test-generation calls use temperature `1.0`; generated environments
are built and pinned before execution. Random rejects nonempty method state and records
`independent: true` in every proposal receipt.

### VeriGrey

VeriGrey first creates one ordered initial seed per frozen property. Each property is only
the generation focus: every materialized test carries the complete frozen catalog. The
entire valid seed corpus is materialized and hash-bound before its first weak-agent
execution. A deterministically invalid seed receives one fresh replacement; two invalid
materializations stop initialization.

After every execution, VeriGrey extracts assistant tool calls from the Pi trace and
replaces argument values with stable type/shape descriptions. Empty tool sequences remain
observable sequences. It counts new tools, transitions, and complete normalized sequences.
Each novelty category contributes one unit of energy, with energy bounded to `1..3`.

All initial seeds execute in catalog order. Mutation then uses a FIFO round-robin queue:
the oldest seed spends all assigned energy before returning to the queue tail. A mutation
receives the selected seed prompt, Dockerfile, tool sequence, novelty evidence, current
skill, and complete catalog. Offspring enter the corpus only when their execution adds a
new tool, transition, or complete sequence. Seed and mutation proposals use temperature
`1.0`, and seed choice, energy, mutation ordinal, observation, and admission are preserved.

Its campaign state contains:

- the initial-seed count, current phase, and total execution count;
- the seed corpus and FIFO queue;
- any seed currently spending its assigned energy;
- tool, transition, and sequence coverage counts; and
- ordered execution observations with corpus-admission decisions.

### SkillRACE

SkillRACE first segments the actual agent trace into ordered, non-overlapping episodes.
Episode boundaries must use real trace event IDs and collectively cover every relevant
assistant thinking/tool-call event and tool-result event. One correction attempt is
allowed for malformed or ungrounded episode output.

The tree stores nodes with purpose, outcome, member run/episode IDs, reach state, and
failure IDs. Exact purpose/outcome matches merge deterministically. If placement is
ambiguous, one batched same-track Pi call selects an existing parent for the complete
episode chain. Edges store the reason for moving to the next episode.

After the ten frozen seed executions, proposal selection works only from observed
episode-to-episode reasoning edges. The host writes a compact edge index with a stable ID,
source purpose, reasoning, target purpose/outcome, observation count, and failure count for
each edge. A fresh tool-free same-track Pi call sees that compact index, the current skill,
and the complete fixed property catalog and returns one exact edge ID plus its selection
rationale.

The host validates the ID and deterministically isolates the root-to-edge branch from the
saved tree. A second fresh tool-free Pi call sees only that isolated branch, selection
rationale, current skill, and fixed properties. It mutates the selected assumption and
returns one prompt and Dockerfile. The mutation must make the assumption fail while keeping
a local recovery route, must not reveal that route in the visible prompt, and must remain
solvable within the unchanged budget. Generated environments use only the pinned base-image
software and local files or symlinks. Exact edge reach is diagnostic; any genuine fixed-
property failure remains useful.
