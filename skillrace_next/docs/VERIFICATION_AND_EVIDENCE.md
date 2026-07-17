# Verification, Docker, Replay, and Evidence

## Trust boundary

SkillRACE Next separates check authoring from authoritative execution:

```text
weak Pi agent in task Docker container
    -> frozen host-mounted artifact and trace
    -> local Codex authors scripts from read-only inputs
    -> deterministic validator accepts/rejects the manifest structure
    -> orchestrator copies scripts into the still-running task container
    -> docker exec runs checks as a restricted UID
    -> Python records authoritative JSON results
```

Codex is not the verdict source and is not the patcher. It writes executable checks. The
Docker execution result is authoritative.

## Codex verifier workspace

The expected workspace is:

```text
verifier/
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
    ├── checks/
    ├── codex-events.jsonl
    └── codex-stderr.txt
```

Before invocation, `input/` and `GUIDE.md` are made read-only and hashed. Codex runs in
`output/` with `workspace-write`, ephemeral mode, user config ignored, Terra model, and
medium reasoning. The implementation sets `DOCKER_HOST` to a nonexistent socket and
removes `DOCKER_CONTEXT`. The prompt and guide explicitly prohibit Docker and any input,
artifact, or skill mutation.

After each call, the code re-hashes the entire input tree and guide. Any mutation is a
hard verifier failure.

Current credential issue: the invocation strips `yunwu_key` but not the Lab credential
variable. This must be corrected even though Codex receives no Docker socket.

## Check manifest

The manifest schema is `skillrace-check-bundle/1` and contains exactly:

```json
{
  "schema": "skillrace-check-bundle/1",
  "run_id": "run-id",
  "artifact_hash": "sha256",
  "checks": [],
  "uncovered": []
}
```

Each declared check includes:

- unique `check_id`;
- supplied `property_id`;
- a relative path below `checks/`;
- a nonempty argv array;
- a timeout from 1 to 60 seconds;
- purpose, pass condition, and failure condition; and
- one fixed root-cause category.

Every supplied NL property must be either covered or explicitly uncovered. Undeclared
scripts, escaping paths, invalid argv, duplicated IDs, unknown properties, wrong hashes,
and unexpected fields invalidate the bundle.

Codex receives one correction call only when the first bundle is structurally invalid.
If the second is also invalid, the implementation writes a valid all-uncovered manifest
with the validator diagnostic.

## Docker checker execution

`execute_checks`:

1. Freezes and hashes the host artifact.
2. Verifies the bundle's artifact hash.
3. Creates `/tmp/skillrace-checks` and a restricted scratch directory.
4. Copies the manifest and declared scripts into the task container.
5. Runs each argv with `docker exec` as UID/GID `65534`.
6. Captures bounded stdout/stderr, exit code, duration, and timeout.
7. Re-hashes the artifact and invalidates all results if it changed.
8. Writes `check_results.json` and per-check streams.
9. Removes the task container and writes `cleanup.json`.

Checker exit meanings:

| Exit | Result |
|---|---|
| `0` | `pass` |
| `1` | `fail` |
| `2` | `inconclusive` |
| timeout, malformed JSON, unexpected exit | `inconclusive` |

Checker stdout must be exactly one JSON object with a nonempty diagnostic and safe,
artifact-relative evidence paths. Infrastructure setup/execution failures are
`inconclusive`; they are never converted into property failures.

## Task container lifecycle

Task containers start from the image ID produced during validation. At startup, Docker
re-inspects the image and refuses a tag whose resolved ID changed. CPU, memory, network,
working directory, host UID/GID, mounts, and credential environment name are explicit.

The weak Pi process runs as a child under GNU `timeout`. An agent timeout kills that child
but should leave the container and partial host artifact available for checks. The
container is removed only after checker evidence is durable.

The intended lifecycle is correct, but cleanup ownership is not yet exception-safe:

- normal checker completion removes the container;
- an exception between `run_agent` and `execute_checks` can leak it;
- `replay` currently raises on a non-completed agent before invoking the checker or
  cleanup.

This must be fixed with explicit lifecycle ownership and `finally` cleanup without
discarding partial artifacts.

## Patch evidence layout

```text
patch/evidence/
├── evidence.json
├── common/
│   ├── skill/
│   │   ├── SKILL.md
│   │   └── skill-version.json
│   ├── test/
│   │   ├── prompt.txt
│   │   ├── environment/
│   │   ├── nl_checks.json
│   │   ├── proposal-receipt.json
│   │   └── test-case.json
│   ├── artifact/
│   ├── run/
│   │   ├── run.json
│   │   ├── trace.jsonl
│   │   ├── tool_outputs.jsonl
│   │   ├── stdout.txt
│   │   └── stderr.txt
│   ├── checks/
│   │   ├── check_manifest.json
│   │   ├── codex-receipt.jsonl
│   │   └── scripts/
│   └── results/
│       ├── check_results.json
│       └── outputs/
└── method/
    ├── verigrey.json
    └── skillrace.json
```

Only the appropriate method file exists. `evidence.json` contains the exact task, result
summaries, relative file map, common-tree hash, method, and run ID. The complete evidence
tree is read-only before patching.

## Replay evidence

Exact replay writes:

```text
replay/
├── run/
│   ├── artifact/
│   ├── runtime/
│   └── run.json
├── check-bundle/
├── check-bundle.json
├── results/
└── replay.json
```

The check scripts and Codex receipt are copied from the original bundle. Only run and
artifact identity are rebound. Acceptance uses `CheckResults`; it never trusts patcher or
Codex prose.

## Live evidence

Paid contracts write below:

```text
out/live-contracts/<component>/<run-id>/
```

Model-parameterized contracts insert the friendly model name:

```text
out/live-contracts/<component>/<model>/<run-id>/
```

Typical evidence includes prompts, artifacts, traces, tool events, usage, model aliases,
provider receipts, image IDs, checker bundles, Docker results, cleanup receipts, and one
terminal component/gate receipt.

Evidence is intentionally not a database and has no migration layer. A run directory is
append-only by stage convention; atomic JSON writes protect terminal receipts.

## Reading failures

Keep these categories distinct:

- `pass`, `fail`, `inconclusive`: checker outcomes;
- `agent_timeout`: experimental weak-agent behavior with a partial artifact;
- `provider_error`, `container_error`, checker infrastructure diagnostics: infrastructure;
- `invalid_test`: pre-agent validation;
- `patch_timeout`, `patch_invalid`: patch-stage outcomes; and
- `accepted`, `rejected`, `unresolved`: deterministic revision decisions.

Do not rerun a weak-agent sample to obtain a more favorable result. A persistent provider
failure blocks the live gate. A wrong patch or slow agent is scientific evidence and must
remain visible.
