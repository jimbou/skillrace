# SkillRACE Clean-Room Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the straightforward Part I and Part II SkillRACE pipeline in `skillrace_next/`, with individually proven live Yunwu/Codex/Docker component contracts and no dependency on the legacy package.

**Architecture:** Implement two explicit sequential loops over a small set of shared stage functions. Port only narrow provider, Pi, Docker, hashing, trace, novelty, episode, and tree behaviors from the old code; all new orchestration, verifier, repair, replay, and iterative-improvement logic is clean-room code. Complete and live-test one self-contained component before building the next.

**Tech Stack:** Python 3.12 standard library, pytest 9, Docker CLI, Pi 0.73.1, Yunwu (`deepseek-v3.2` for development and `glm-4.7` for the final dual-model gate), Codex CLI, JSON/JSONL.

---

## Read this first

The binding design is:

- `docs/superpowers/specs/2026-07-17-skillrace-clean-room-pipeline-design.md`
- `/home/jim/skillrace/updated_pipeline.md`

The working tree contains extensive user changes. Do not clean, reset, move, reformat, or
commit any unrelated path. Until the final user-approved cutover, implementation changes
are limited to:

- `skillrace_next/**`
- `tests_next/**`
- `docs/superpowers/plans/2026-07-17-skillrace-clean-room-rebuild.md`
- short phase evidence under `out/live-contracts/**` (normally ignored, never commit
  secrets or raw credentials)

Do not start by auditing the repository again or writing another plan. The pipeline is
already designed. Implement the tasks below in order. A task is complete only after its
offline tests and named individual live contract test pass. A later end-to-end run does
not replace an earlier component test.

For every live command:

- require `yunwu_key` without printing it;
- require an explicit `--live` pytest option;
- use development-only fixtures;
- save sanitized model ID, operation ID, request/response or Pi trace, token usage,
  timeout, and evidence paths under `out/live-contracts/<component>/<run-id>/`;
- permit only the one retry allowed by the design;
- stop on persistent 429/5xx instead of faking success; and
- manually inspect the first successful semantic output before checking the task off.

## Locked file map

Create files only when their task begins:

```text
skillrace_next/
  __init__.py                 package marker only
  __main__.py                 calls cli.main()
  cli.py                      four public commands; no internal-stage CLIs
  config.py                   one frozen ExperimentConfig loader
  records.py                  eight durable records and small result records
  storage.py                  canonical JSON, hashing, atomic writes
  runtime/
    __init__.py
    pi.py                     direct preflight and one bounded Pi invocation primitive
    docker.py                 task-container start/exec/cleanup
    artifacts.py              tree hashing, freezing, artifact receipts
  verification/
    __init__.py
    GUIDE.md                  immutable-artifact verifier contract
    codex.py                  Codex invocation and check-bundle validation
    executor.py               docker exec and authoritative result capture
  methods/
    __init__.py
    random.py                 independent proposal construction
    verigrey.py               normalized tool-sequence state and proposal context
    skillrace.py              episode creation, behavior-tree merge, branch selection
  pipeline/
    __init__.py
    stages.py                 concrete shared proposal/run/verify/patch/replay functions
    part1.py                  immutable-S0 discovery loop
    part2.py                  cumulative-Si improvement loop
  analysis/
    __init__.py
    part1.py                  discovery and repair metrics
    part2.py                  held-out improvement metrics

tests_next/
  conftest.py                 --live gate and evidence-root fixture
  fixtures/                   tiny development-only skills, tests, environments, traces
  unit/                       pure contract tests
  integration/                local Docker tests
  live/                       one file per online component contract
```

Do not add factories, registries, managers, services, repositories, schema migration
packages, workflow engines, or plugin APIs.

### Task 1: Establish the clean-room boundary and live-test switch

**Files:**
- Create: `skillrace_next/__init__.py`
- Create: `skillrace_next/__main__.py`
- Create: `skillrace_next/cli.py`
- Create: `tests_next/conftest.py`
- Create: `tests_next/unit/test_package_boundary.py`
- Create: `tests_next/unit/test_cli.py`

- [ ] **Step 1: Write the failing package-boundary test**

```python
# tests_next/unit/test_package_boundary.py
import ast
from pathlib import Path


def test_clean_room_package_exists_and_never_imports_legacy() -> None:
    root = Path("skillrace_next")
    assert root.is_dir()
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            if any(name == "skillrace" or name.startswith("skillrace.") for name in names):
                offenders.append(str(path))
    assert offenders == []
```

- [ ] **Step 2: Run the test and verify it fails because `skillrace_next/` is absent**

Run: `.venv/bin/python -m pytest tests_next/unit/test_package_boundary.py -v`

Expected: FAIL at `assert root.is_dir()`.

Add this CLI test before implementing the parser:

```python
# tests_next/unit/test_cli.py
import pytest
from skillrace_next.cli import build_parser


@pytest.mark.parametrize("command", ["live-smoke", "part1", "part2", "analyze"])
def test_only_four_public_commands_parse(command: str) -> None:
    option = "--run" if command == "analyze" else "--config"
    value = "run-dir" if command == "analyze" else "config.json"
    parsed = build_parser().parse_args([command, option, value])
    assert parsed.command == command


def test_internal_stage_is_not_a_command() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["author-checks"])
```

- [ ] **Step 3: Create the minimal package and CLI**

```python
# skillrace_next/__main__.py
from .cli import main

raise SystemExit(main())
```

```python
# skillrace_next/cli.py
import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m skillrace_next")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("live-smoke", "part1", "part2", "analyze"):
        command = sub.add_parser(name)
        command.add_argument("--config") if name != "analyze" else command.add_argument("--run")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
```

Keep `__init__.py` empty except for a one-line package docstring.

- [ ] **Step 4: Add the explicit paid-test switch**

```python
# tests_next/conftest.py
from pathlib import Path
import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--live", action="store_true", default=False)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="requires --live and may spend model budget")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def live_evidence_root() -> Path:
    root = Path("out/live-contracts")
    root.mkdir(parents=True, exist_ok=True)
    return root
```

- [ ] **Step 5: Test CLI help and package boundary**

Run: `.venv/bin/python -m pytest tests_next/unit/test_package_boundary.py tests_next/unit/test_cli.py -v`

Expected: PASS; `python -m skillrace_next --help` exposes only the four commands.

- [ ] **Step 6: Commit only Task 1 files**

```bash
git add skillrace_next/__init__.py skillrace_next/__main__.py skillrace_next/cli.py tests_next/conftest.py tests_next/unit/test_package_boundary.py tests_next/unit/test_cli.py
git commit -m "feat(next): establish clean-room package boundary"
```

### Task 2: Add canonical storage, config freezing, and durable records

**Files:**
- Create: `skillrace_next/storage.py`
- Create: `skillrace_next/config.py`
- Create: `skillrace_next/records.py`
- Create: `tests_next/unit/test_storage.py`
- Create: `tests_next/unit/test_config.py`
- Create: `tests_next/unit/test_records.py`
- Create: `tests_next/fixtures/development.deepseek-v3.2.json`

- [ ] **Step 1: Write failing tests for canonical JSON and tree hashes**

Cover these exact contracts:

```python
assert canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
assert canonical_json_hash({"a": 2, "b": 1}) == canonical_json_hash({"b": 1, "a": 2})
assert tree_hash(first_directory) == tree_hash(byte_identical_copy)
```

Also simulate an exception before `os.replace` and assert `atomic_write_json` preserves
the prior file.

- [ ] **Step 2: Run storage tests and verify import failures**

Run: `.venv/bin/python -m pytest tests_next/unit/test_storage.py -v`

Expected: collection ERROR because `skillrace_next.storage` does not exist.

- [ ] **Step 3: Port only the five small storage primitives**

Implement in `storage.py`:

```text
canonical_json_bytes(value) -> UTF-8 JSON bytes with sorted keys and compact separators
canonical_json_hash(value) -> SHA-256 of canonical_json_bytes
file_hash(path) -> streaming SHA-256 of one file
tree_hash(path) -> SHA-256 over sorted relative paths and file contents
atomic_write_json(path, value) -> fsynced temporary file followed by os.replace
```

Port the relevant logic from `skillrace/io_utils.py`; do not import that module and do not
port its legacy path-resolution behavior.

- [ ] **Step 4: Write failing config and record round-trip tests**

The tests must instantiate all eight design records and prove `to_dict()`/`from_dict()`
round trips. Test that an `ExperimentConfig` rejects unknown keys, a non-Yunwu provider,
missing timeouts, or a verifier role set to Pi.

- [ ] **Step 5: Implement the exact record surface**

Use frozen dataclasses for:

```python
ExperimentConfig
SkillVersion
TestCase
RunRecord
CheckBundle
CheckResults
PatchAttempt
ImprovementStep
```

Each has a literal `/1` schema in serialized JSON. Keep path fields as strings in JSON
and `Path` objects in Python. Do not create a generic schema registry.

- [ ] **Step 6: Freeze and hash one development config**

The fixture must select `yunwu`, `deepseek-v3.2`, Pi `0.73.1`, Codex `gpt-5.6` with
`high` reasoning, bounded timeouts from the design, and output under `out/development`.
Implement:

```text
load_config(path) -> validated ExperimentConfig, rejecting unknown fields
freeze_config(config, output) -> write normalized config.json/config.sha256 and return hash
```

- [ ] **Step 7: Run focused and boundary tests**

Run: `.venv/bin/python -m pytest tests_next/unit/test_storage.py tests_next/unit/test_config.py tests_next/unit/test_records.py tests_next/unit/test_package_boundary.py -v`

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```bash
git add skillrace_next/storage.py skillrace_next/config.py skillrace_next/records.py tests_next/unit/test_storage.py tests_next/unit/test_config.py tests_next/unit/test_records.py tests_next/fixtures/development.deepseek-v3.2.json
git commit -m "feat(next): add minimal records and frozen config"
```

### Task 3: Port the minimal Yunwu and Pi invocation boundary

**Files:**
- Create: `skillrace_next/runtime/__init__.py`
- Create: `skillrace_next/runtime/pi.py`
- Create: `tests_next/unit/test_pi_runtime.py`
- Create: `tests_next/live/test_pi_runtime_live.py`

- [ ] **Step 1: Write the fake-subprocess contract test**

The test must inject a fake runner and assert the command selects provider `yunwu`, the
requested model, bounded turns/timeout, a supplied prompt, explicit allowed tools, and a
dedicated accounting directory. Assert that returned evidence contains operation ID,
model, status, trace path, usage, and sanitized stderr—but never `yunwu_key`.

- [ ] **Step 2: Verify the unit test fails before implementation**

Run: `.venv/bin/python -m pytest tests_next/unit/test_pi_runtime.py -v`

Expected: collection ERROR for `skillrace_next.runtime.pi`.

- [ ] **Step 3: Implement one bounded Pi primitive and one preflight**

```python
@dataclass(frozen=True)
class PiRequest:
    operation_id: str
    model: str
    prompt_path: Path
    output_dir: Path
    image: str
    allowed_tools: tuple[str, ...]
    max_turns: int
    timeout_seconds: int
    mounts: tuple[tuple[Path, str, str], ...] = ()


run_pi(request, injected_subprocess_runner) -> terminal PiResult and durable evidence
direct_yunwu_preflight(model, evidence_dir) -> one bounded ProviderProbe
```

Selectively copy the redaction, operation journaling, usage parsing, and model-catalog
handling needed from `closeai.py`/`pi_patcher.py`. Do not copy model policy tables,
campaign schemas, repair logic, or compatibility readers. Accept one retry only in the
preflight wrapper.

- [ ] **Step 4: Write the real individual live test**

`test_pi_runtime_live.py` must:

1. skip unless `--live` and `yunwu_key` are present;
2. make one direct `deepseek-v3.2` preflight;
3. make one Pi call that uses a harmless read/write fixture tool;
4. assert a structured tool call occurred;
5. assert trace and usage evidence exist; and
6. assert saved evidence contains no API-key value.

- [ ] **Step 5: Run offline tests**

Run: `.venv/bin/python -m pytest tests_next/unit/test_pi_runtime.py tests_next/unit/test_package_boundary.py -v`

Expected: PASS.

- [ ] **Step 6: Run and manually inspect the Pi live contract**

Run: `.venv/bin/python -m pytest tests_next/live/test_pi_runtime_live.py --live -v -s`

Expected: PASS with one direct and one Pi receipt under
`out/live-contracts/pi-runtime/`. On persistent provider error, preserve the sanitized
blocked receipt and stop; do not begin Task 4.

- [ ] **Step 7: Commit Task 3 after the live contract passes**

```bash
git add skillrace_next/runtime tests_next/unit/test_pi_runtime.py tests_next/live/test_pi_runtime_live.py
git commit -m "feat(next): add live-verified Yunwu Pi boundary"
```

### Task 4: Build the task-container runner and artifact freezer

**Files:**
- Create: `skillrace_next/runtime/docker.py`
- Create: `skillrace_next/runtime/artifacts.py`
- Create: `tests_next/fixtures/task/`
- Create: `tests_next/unit/test_artifacts.py`
- Create: `tests_next/integration/test_task_container.py`
- Create: `tests_next/live/test_task_runner_live.py`

- [ ] **Step 1: Write failing artifact tests**

Test that freezing makes a tree non-writable to a different numeric UID, returns a stable
content/path hash, preserves partial files, and detects any content mutation.

- [ ] **Step 2: Implement the artifact functions directly**

```text
freeze_artifact(path, checker_uid) -> read-only FrozenArtifact with content/path hash
verify_artifact_unchanged(frozen) -> compare current tree hash with frozen hash
```

Do not create snapshot registries or lifecycle objects.

- [ ] **Step 3: Write the local Docker integration test**

Use a tiny local image and assert this exact sequence works:

```text
start inert container -> docker exec child -> host artifact appears -> capture result -> cleanup
```

Add a timeout case where the child is killed, the container remains available, the
partial artifact is frozen, and cleanup happens only after capture.
Build the fixture image once, record its image ID, and assert the real task start reuses
that ID rather than rebuilding the Dockerfile.

- [ ] **Step 4: Implement four Docker functions**

```text
start_task_container(spec) -> RunningContainer with inert supervisor
exec_task(container, argv, timeout_seconds) -> captured ExecResult
copy_into_container(container, source, destination) -> checked docker cp
remove_container(container) -> idempotent CleanupResult
```

Use argv lists and `subprocess.run`. No detached cleanup process, scheduler, context
registry, or recovery fold.

- [ ] **Step 5: Write and run the real task-agent contract**

The live test starts the development task container, runs Pi/Yunwu `deepseek-v3.2`, asks
the weak agent to create a small specified artifact, then asserts artifact, trace, tool
outputs, usage, and cleanup receipts are durable.

Run offline: `.venv/bin/python -m pytest tests_next/unit/test_artifacts.py tests_next/integration/test_task_container.py -v`

Run live: `.venv/bin/python -m pytest tests_next/live/test_task_runner_live.py --live -v -s`

Expected: both PASS; real evidence under `out/live-contracts/task-runner/`.

- [ ] **Step 6: Commit Task 4**

```bash
git add skillrace_next/runtime/docker.py skillrace_next/runtime/artifacts.py tests_next/fixtures/task tests_next/unit/test_artifacts.py tests_next/integration/test_task_container.py tests_next/live/test_task_runner_live.py
git commit -m "feat(next): preserve real task-container artifacts"
```

### Task 5: Implement validation and the independent Random proposer

**Files:**
- Create: `skillrace_next/methods/__init__.py`
- Create: `skillrace_next/methods/random.py`
- Create: `skillrace_next/pipeline/__init__.py`
- Create: `skillrace_next/pipeline/stages.py`
- Create: `tests_next/unit/test_test_cases.py`
- Create: `tests_next/unit/test_random_method.py`
- Create: `tests_next/live/test_test_proposer_live.py`

- [ ] **Step 1: Write failing deterministic validation tests**

Test valid prompt/environment/NL-check inputs and rejection of missing files, malformed
property IDs, escaping paths, failed Docker build, and invalid sanity receipt. Invalid
tests must return `invalid_test`, not a bug or agent failure.

- [ ] **Step 2: Implement only the concrete validators**

```text
validate_test(test, config) -> validated TestCase or explicit invalid_test result
validate_nl_checks(path) -> ordered property dictionaries with unique IDs
```

In `pipeline/stages.py`, also add the direct wrapper
`run_agent(skill, test, config, output_dir) -> RunRecord`; it starts one validated image
through the Task 4 runtime, runs the same-track weak Pi model, freezes evidence, and
returns the still-live container identity needed by check execution. It must not rebuild
the validated image. The surrounding Part I/Part II attempt owns one `try/finally` and
removes that container only after check evidence is durable.

- [ ] **Step 3: Write the Random proposer test and minimal method**

`random.py` contains no adaptive state. It builds a Pi prompt from properties and asks for
one independent test proposal. Parse one strict JSON response into `TestCase`; permit one
format-correction call and no semantic retry.

At the campaign level, permit one replacement proposal after deterministic validation
rejects the first proposal. The execution budget counts weak-agent runs, not proposal
calls; test this explicitly in `test_random_method.py`.

- [ ] **Step 4: Run the individual Yunwu proposal contract**

Run offline: `.venv/bin/python -m pytest tests_next/unit/test_test_cases.py tests_next/unit/test_random_method.py -v`

Run live: `.venv/bin/python -m pytest tests_next/live/test_test_proposer_live.py --live -v -s`

Expected: the live proposal passes deterministic environment/test validation and saves
its Pi trace under `out/live-contracts/test-proposer/`.

- [ ] **Step 5: Commit Task 5**

```bash
git add skillrace_next/methods skillrace_next/pipeline tests_next/unit/test_test_cases.py tests_next/unit/test_random_method.py tests_next/live/test_test_proposer_live.py
git commit -m "feat(next): add validated independent test proposals"
```

### Task 6: Implement Codex check authoring without Docker access

**Files:**
- Create: `skillrace_next/verification/__init__.py`
- Create: `skillrace_next/verification/GUIDE.md`
- Create: `skillrace_next/verification/codex.py`
- Create: `tests_next/unit/test_check_manifest.py`
- Create: `tests_next/unit/test_codex_verifier.py`
- Create: `tests_next/live/test_codex_verifier_live.py`

- [ ] **Step 1: Copy the verifier contract exactly into GUIDE.md**

Use Section 8.2 of the design. It must say the artifact and skill are immutable, the job
is checking rather than repairing, only `output/` is writable, local exploration is not a
verdict, and indefensible properties must be uncovered rather than guessed.

- [ ] **Step 2: Write failing manifest-validation tests**

Cover valid manifests, missing property coverage, escaping script paths, non-list argv,
timeouts outside `1..60`, unknown root-cause category, undeclared scripts, and mismatched
artifact hash.

- [ ] **Step 3: Implement the small validator and Codex invocation**

```text
validate_check_manifest(path, nl_checks, artifact_hash) -> bound CheckBundle
author_checks(workspace, config) -> one validated CheckBundle or inconclusive bundle
```

Invoke Codex with the working directory set to `verifier_workspace/output`, sandbox
`workspace-write`, model `gpt-5.6`, reasoning `high`, JSONL event output, and a prompt that
points to `../GUIDE.md` and `../input/`. Codex receives no Docker socket, container ID, or
Docker tool. Hash all inputs before and after. Allow one correction call only for a
structurally invalid bundle.

- [ ] **Step 4: Unit-test with a fake Codex executable**

The fake writes a valid bundle to output. Assert only output changes and input mutation
causes verifier failure.

- [ ] **Step 5: Run the individual real Codex contract**

Use a preserved artifact from a real Yunwu task-runner fixture, no synthetic path-only
listing. Supply at most two NL checks.

Run offline: `.venv/bin/python -m pytest tests_next/unit/test_check_manifest.py tests_next/unit/test_codex_verifier.py -v`

Run live: `.venv/bin/python -m pytest tests_next/live/test_codex_verifier_live.py --live -v -s`

Expected: PASS, unchanged inputs, a semantically reviewed bundle, and Codex events under
`out/live-contracts/codex-verifier/`.

- [ ] **Step 6: Commit Task 6**

```bash
git add skillrace_next/verification tests_next/unit/test_check_manifest.py tests_next/unit/test_codex_verifier.py tests_next/live/test_codex_verifier_live.py
git commit -m "feat(next): author immutable-artifact checks with Codex"
```

### Task 7: Execute authored checks in Docker and write authoritative results

**Files:**
- Create: `skillrace_next/verification/executor.py`
- Create: `tests_next/unit/test_check_results.py`
- Create: `tests_next/integration/test_check_executor.py`
- Create: `tests_next/live/test_check_executor_live.py`

- [ ] **Step 1: Write exit/status mapping tests**

Assert `0 -> pass`, `1 -> fail`, `2 -> inconclusive`, and timeout, malformed JSON,
unexpected exit, or artifact mutation all become `inconclusive`/invalid checker outcomes,
never property failures.

- [ ] **Step 2: Implement one explicit executor loop**

```text
execute_checks(container, artifact, bundle, output_dir) -> for each declared check,
docker exec as restricted UID, capture its JSON/streams/status, verify the artifact hash,
write one authoritative check_results.json, and return CheckResults
```

Copy the bundle to `/tmp/skillrace-checks`, run each argv with `docker exec --user` and a
writable scratch directory, capture bounded stdout/stderr, write one results JSON, and
re-hash the artifact. Do not add checker backends or shell-policy parsing.

- [ ] **Step 3: Pass the local Docker integration test**

Run: `.venv/bin/python -m pytest tests_next/unit/test_check_results.py tests_next/integration/test_check_executor.py -v`

Expected: PASS for pass/fail/inconclusive, timeout, and mutation fixtures.

- [ ] **Step 4: Run the real Codex-authored bundle in the real task container**

Run: `.venv/bin/python -m pytest tests_next/live/test_check_executor_live.py --live -v -s`

Expected: PASS with `check_results.json`, stdout/stderr, unchanged artifact, and evidence
under `out/live-contracts/check-executor/`. This test uses real Codex plus Docker; do not
replace Codex with Yunwu.

- [ ] **Step 5: Commit Task 7**

```bash
git add skillrace_next/verification/executor.py tests_next/unit/test_check_results.py tests_next/integration/test_check_executor.py tests_next/live/test_check_executor_live.py
git commit -m "feat(next): execute authoritative checks in Docker"
```

### Task 8: Implement and live-test the episode creator

**Files:**
- Create: `skillrace_next/methods/skillrace.py`
- Create: `tests_next/unit/test_episode_creator.py`
- Create: `tests_next/fixtures/traces/`
- Create: `tests_next/live/test_episode_creator_live.py`

- [ ] **Step 1: Define and test the exact episode schema**

Each episode has only:

```python
{
    "episode_id": str,
    "start_event_id": str,
    "end_event_id": str,
    "purpose": str,
    "outcome": str,
    "reason_for_next": str | None,
}
```

Validate ordering, non-overlap, referenced trace event IDs, complete coverage of relevant
reasoning/tool events, and nonempty purpose/outcome. Reject free-floating summaries that
cannot be grounded in the trace.

- [ ] **Step 2: Implement one Pi call plus deterministic validation**

```text
create_episodes(run, config, output_dir) -> one ordered, validated episode list and Pi receipt
```

Use the same track model through `run_pi`. Permit one correction only for invalid JSON.
Do not introduce a segmenter class or alternate direct-model backend.

- [ ] **Step 3: Run offline tests with a fake Pi response**

Run: `.venv/bin/python -m pytest tests_next/unit/test_episode_creator.py -v`

Expected: PASS for valid, overlapping, ungrounded, and missing-field cases.

- [ ] **Step 4: Run and semantically inspect the individual Yunwu episode contract**

Run: `.venv/bin/python -m pytest tests_next/live/test_episode_creator_live.py --live -v -s`

Expected: one real saved agent trace is segmented by Pi/Yunwu, the episodes are ordered
and source-grounded, and evidence is under `out/live-contracts/episode-creator/`.

- [ ] **Step 5: Commit Task 8**

```bash
git add skillrace_next/methods/skillrace.py tests_next/unit/test_episode_creator.py tests_next/fixtures/traces tests_next/live/test_episode_creator_live.py
git commit -m "feat(next): create grounded reasoning episodes"
```

### Task 9: Implement and live-test the SkillRACE tree merger

**Files:**
- Modify: `skillrace_next/methods/skillrace.py`
- Create: `tests_next/unit/test_tree_merge.py`
- Create: `tests_next/live/test_tree_merge_live.py`

- [ ] **Step 1: Write tree invariant tests**

Use a plain JSON state containing nodes, reasoning-labelled edges, run/episode membership,
reach status, and attached failure IDs. Test deterministic exact placement, one ambiguous
placement, duplicate membership rejection, and preservation of existing branches.

- [ ] **Step 2: Implement deterministic merge first**

```text
merge_episodes(tree, episodes, run_id, failures, config, output_dir) -> validated tree
select_unreached_branch(tree) -> one deterministic branch target or None
propose_test(tree, skill, config) -> one Pi/Yunwu TestCase targeting that branch
```

Use no model call when placement is exact. For all ambiguous placements in one run, make
at most one batched Pi/Yunwu alignment call. Do not call a model once per node.

- [ ] **Step 3: Run offline invariant tests**

Run: `.venv/bin/python -m pytest tests_next/unit/test_tree_merge.py -v`

Expected: PASS.

- [ ] **Step 4: Run and inspect the individual Yunwu merge contract**

Run: `.venv/bin/python -m pytest tests_next/live/test_tree_merge_live.py --live -v -s`

Expected: real episodes take the ambiguous path through one Pi/Yunwu call; the resulting
tree preserves nodes, edges, membership, reach state, and failure links under
`out/live-contracts/tree-merger/`.

- [ ] **Step 5: Run the separate SkillRACE branch-proposal contract**

Create `tests_next/live/test_skillrace_proposal_live.py`. Give it the saved real tree,
select one unreached branch deterministically, make one Pi/Yunwu proposal, and assert the
validated test records that branch as its exploration target.

Run: `.venv/bin/python -m pytest tests_next/live/test_skillrace_proposal_live.py --live -v -s`

Expected: PASS with evidence under `out/live-contracts/skillrace-proposer/`.

- [ ] **Step 6: Commit Task 9**

```bash
git add skillrace_next/methods/skillrace.py tests_next/unit/test_tree_merge.py tests_next/live/test_tree_merge_live.py tests_next/live/test_skillrace_proposal_live.py
git commit -m "feat(next): merge episodes into reasoning tree"
```

### Task 10: Implement and live-test VeriGrey novelty state

**Files:**
- Create: `skillrace_next/methods/verigrey.py`
- Create: `tests_next/unit/test_verigrey.py`
- Create: `tests_next/live/test_verigrey_live.py`

- [ ] **Step 1: Test normalized tool sequences**

Keep tool names and stable argument shapes; remove volatile values. Test coverage counts,
novel-transition selection, and the exact tool-sequence evidence included for patching.
Do not implement epochs, reservations, or parallel completion.

- [ ] **Step 2: Implement plain functions and JSON state**

```text
normalize_tool_sequence(trace) -> stable tool-name/argument-shape sequence
update_state(state, sequence) -> copied JSON state with coverage counts updated
propose_test(state, skill, config) -> one Pi/Yunwu TestCase targeting novelty
```

- [ ] **Step 3: Pass offline tests**

Run: `.venv/bin/python -m pytest tests_next/unit/test_verigrey.py -v`

Expected: PASS.

- [ ] **Step 4: Run the individual Yunwu novelty-backed proposal**

Run: `.venv/bin/python -m pytest tests_next/live/test_verigrey_live.py --live -v -s`

Expected: one real Pi/Yunwu proposal cites the supplied novelty target, validates as a
test, and saves evidence under `out/live-contracts/verigrey/`.

- [ ] **Step 5: Commit Task 10**

```bash
git add skillrace_next/methods/verigrey.py tests_next/unit/test_verigrey.py tests_next/live/test_verigrey_live.py
git commit -m "feat(next): add tool-sequence novelty state"
```

### Task 11: Implement and live-test Part II base-skill generation

**Files:**
- Modify: `skillrace_next/pipeline/stages.py`
- Create: `tests_next/unit/test_skill_generation.py`
- Create: `tests_next/live/test_skill_generation_live.py`

- [ ] **Step 1: Test generation isolation and identity**

Assert one generation produces one valid `SKILL.md`, a `SkillVersion` with no parent,
trace/usage receipt, and byte-identical method copies. Reject extra writes outside the
skill directory.

- [ ] **Step 2: Implement one generation function**

```text
generate_base_skill(scenario, config, output_dir) -> validated S0 SkillVersion plus Pi evidence
```

Use Pi and the same track model. Do not add template/reviser modes or generation
backends.

- [ ] **Step 3: Run offline then individual live generation tests**

Run offline: `.venv/bin/python -m pytest tests_next/unit/test_skill_generation.py -v`

Run live: `.venv/bin/python -m pytest tests_next/live/test_skill_generation_live.py --live -v -s`

Expected: PASS with a semantically reviewed development skill under
`out/live-contracts/skill-generator/`.

- [ ] **Step 4: Commit Task 11**

```bash
git add skillrace_next/pipeline/stages.py tests_next/unit/test_skill_generation.py tests_next/live/test_skill_generation_live.py
git commit -m "feat(next): generate one isolated base skill"
```

### Task 12: Build common/method evidence and the one Pi patcher

**Files:**
- Modify: `skillrace_next/pipeline/stages.py`
- Create: `tests_next/unit/test_patch_evidence.py`
- Create: `tests_next/unit/test_patcher.py`
- Create: `tests_next/live/test_patcher_live.py`

- [ ] **Step 1: Test exact evidence equality and differences**

Assert common evidence is byte-identical across methods. Random adds nothing, VeriGrey
adds only tool-sequence/novelty evidence, and SkillRACE adds only episodes/tree/branch
evidence. Include actual check scripts and authoritative result paths, not guessed paths.

- [ ] **Step 2: Implement evidence construction directly**

```text
build_patch_evidence(method, state, run, results, output_dir) -> immutable evidence directory and hash
```

- [ ] **Step 3: Test a fake patcher before the live backend**

The fake must read `SKILL.md` and evidence before editing, change only `SKILL.md`, and
return a `PatchAttempt`. Assert mutation of any artifact/check/test/environment file
invalidates the attempt.

- [ ] **Step 4: Implement one guided Pi patch function**

```text
patch_skill(skill, evidence, method, config, output_dir) -> one terminal PatchAttempt
```

Selectively port the guided read-then-edit behavior from `pi_patcher.py`. Remove its old
request/reviser schemas. Use one prompt/backend for all methods, the same model as the
weak agent, six turns, 300 seconds, and tools limited to reading evidence plus editing
`SKILL.md`.

- [ ] **Step 5: Run the individual real Yunwu patch contract**

Use one manually defensible development failure from a real Yunwu run and its real check
bundle/results.

Run offline: `.venv/bin/python -m pytest tests_next/unit/test_patch_evidence.py tests_next/unit/test_patcher.py -v`

Run live: `.venv/bin/python -m pytest tests_next/live/test_patcher_live.py --live -v -s`

Expected: PASS, exactly one changed `SKILL.md`, real trace/usage, and evidence under
`out/live-contracts/patcher/`. Manually confirm the edit addresses the evidence without
memorizing test values.

- [ ] **Step 6: Commit Task 12**

```bash
git add skillrace_next/pipeline/stages.py tests_next/unit/test_patch_evidence.py tests_next/unit/test_patcher.py tests_next/live/test_patcher_live.py
git commit -m "feat(next): patch skills through one Pi path"
```

### Task 13: Implement exact replay and deterministic acceptance

**Files:**
- Modify: `skillrace_next/pipeline/stages.py`
- Create: `tests_next/unit/test_patch_acceptance.py`
- Create: `tests_next/integration/test_exact_replay.py`
- Create: `tests_next/live/test_exact_replay_live.py`

- [ ] **Step 1: Write the complete acceptance truth table**

Test at least:

```text
fail->pass plus all pass->pass = accept
fail->fail = reject
fail->inconclusive = reject
pass->fail = reject
pass->inconclusive = reject
infrastructure error = unresolved
```

- [ ] **Step 2: Implement pure acceptance and one replay function**

```text
accept_patch(before, replay, regressions) -> accepted, rejected, or unresolved
replay(skill, test, bundle, config, output_dir) -> CheckResults from a fresh exact run
```

Replay starts a new clean task container with the same environment, prompt, weak model,
Pi version, budget, and exact saved scripts. It never reuses the failed final container.

- [ ] **Step 3: Run unit and Docker integration tests**

Run: `.venv/bin/python -m pytest tests_next/unit/test_patch_acceptance.py tests_next/integration/test_exact_replay.py -v`

Expected: PASS.

- [ ] **Step 4: Run the individual real Yunwu replay contract**

Run: `.venv/bin/python -m pytest tests_next/live/test_exact_replay_live.py --live -v -s`

Expected: a real patched skill is executed by the weak Yunwu model, the exact saved checks
run, and the deterministic decision is saved under `out/live-contracts/exact-replay/`.

- [ ] **Step 5: Commit Task 13**

```bash
git add skillrace_next/pipeline/stages.py tests_next/unit/test_patch_acceptance.py tests_next/integration/test_exact_replay.py tests_next/live/test_exact_replay_live.py
git commit -m "feat(next): replay and accept patches deterministically"
```

### Task 14: Assemble the immutable-S0 Part I loop and metrics

**Files:**
- Create: `skillrace_next/pipeline/part1.py`
- Create: `skillrace_next/analysis/__init__.py`
- Create: `skillrace_next/analysis/part1.py`
- Create: `tests_next/unit/test_part1_grouping.py`
- Create: `tests_next/integration/test_part1_loop.py`
- Create: `tests_next/live/test_part1_tiny_live.py`

- [ ] **Step 1: Test grouping before patching**

Use fixtures with repeated failures. Assert the key is
`(property_group, failing_check_signature, root_cause_category)`, exactly one
representative is patched, raw candidates and confirmed bugs remain separate, and every
discovery run references the same `S0` hash.
Also assert all non-verifier model receipts in a track use the same model ID and every
method records the same Pi patcher backend.

- [ ] **Step 2: Implement the loop as ordinary nested loops**

```text
for each method:
    initialize state
    for each budget slot:
        propose -> validate -> run S0 -> author/execute checks -> update state
group all candidates before any patch
for each group representative:
    confirm with unchanged S0 -> patch fresh S0 -> exact replay -> record decision
write summary and return the experiment directory
```

No scheduler, epoch, reservation, lifecycle engine, or alternate checker/patch path.

- [ ] **Step 3: Implement only required Part I metrics**

Report raw candidates, confirmed distinct bugs, confirmed repaired bugs, repair rate,
inconclusive/infrastructure counts, and stage costs. Discovery must not depend on repair
success.

- [ ] **Step 4: Pass the deterministic integration fixture**

Run: `.venv/bin/python -m pytest tests_next/unit/test_part1_grouping.py tests_next/integration/test_part1_loop.py -v`

Expected: PASS with three methods and immutable `S0`.

- [ ] **Step 5: Run tiny real Part I with each method**

Run: `.venv/bin/python -m pytest tests_next/live/test_part1_tiny_live.py --live -v -s`

Expected: exactly one weak-agent execution per method, real state updates, group-before-
patch behavior, at most one representative repair per method, and evidence under
`out/live-contracts/part1/`.

- [ ] **Step 6: Commit Task 14**

```bash
git add skillrace_next/pipeline/part1.py skillrace_next/analysis tests_next/unit/test_part1_grouping.py tests_next/integration/test_part1_loop.py tests_next/live/test_part1_tiny_live.py
git commit -m "feat(next): run immutable-S0 discovery campaigns"
```

### Task 15: Assemble cumulative Part II improvement and held-out evaluation

**Files:**
- Create: `skillrace_next/pipeline/part2.py`
- Create: `skillrace_next/analysis/part2.py`
- Create: `tests_next/unit/test_improvement_steps.py`
- Create: `tests_next/integration/test_part2_loop.py`
- Create: `tests_next/integration/test_heldout_isolation.py`
- Create: `tests_next/live/test_part2_tiny_live.py`

- [ ] **Step 1: Write accepted/rejected version-chain tests**

Assert all methods receive byte-identical `S0`; accepted patches produce `Si+1`; rejected
or unresolved patches retain `Si`; all previously retained dev tests replay before
acceptance; held-out paths are absent before final evaluation.
Also assert non-verifier roles use one same-track model and held-out repetitions equal the
frozen config value.

- [ ] **Step 2: Implement the loop directly**

```text
generate one S0
for each method:
    current = byte-identical copy of S0
    for each development iteration:
        select -> run current -> predefined checks -> update state
        on failure: patch -> exact replay -> retained regression runs -> accept/reject
    evaluate current on inaccessible-until-now held-out tests
write summary and return the experiment directory
```

Use predefined checks only. Do not call Codex during Part II benchmark evaluation. Do not
add feedback envelopes or one-shot revisers.

- [ ] **Step 3: Implement required held-out metrics**

Report per-test pass rate, all-tests-pass rate, scenario mean/median, pairwise wins,
regressions from `S0`, accepted/rejected revisions, and cost.

- [ ] **Step 4: Pass integration and isolation tests**

Run: `.venv/bin/python -m pytest tests_next/unit/test_improvement_steps.py tests_next/integration/test_part2_loop.py tests_next/integration/test_heldout_isolation.py -v`

Expected: PASS.

- [ ] **Step 5: Run tiny real two-iteration Part II**

Run: `.venv/bin/python -m pytest tests_next/live/test_part2_tiny_live.py --live -v -s`

Expected: exactly two iterations per method, accepted revisions carried forward, rejected
revisions discarded, one held-out run per final skill, and evidence under
`out/live-contracts/part2/`.

- [ ] **Step 6: Commit Task 15**

```bash
git add skillrace_next/pipeline/part2.py skillrace_next/analysis/part2.py tests_next/unit/test_improvement_steps.py tests_next/integration/test_part2_loop.py tests_next/integration/test_heldout_isolation.py tests_next/live/test_part2_tiny_live.py
git commit -m "feat(next): evolve skills through cumulative iterations"
```

### Task 16: Connect the CLI, prove documented commands, and run the dual-model gate

**Files:**
- Modify: `skillrace_next/cli.py`
- Create: `tests_next/unit/test_documented_cli.py`
- Create: `tests_next/live/test_dual_model_gate_live.py`
- Create: `skillrace_next/README.md`

- [ ] **Step 1: Connect only the four public commands**

`live-smoke` selects one named component or the bounded Gate E/F suite; `part1` and
`part2` load/freeze config and invoke their loop; `analyze` reads existing receipts and
writes summaries. Do not expose internal stages as commands.

- [ ] **Step 2: Test every documented command**

Run: `.venv/bin/python -m pytest tests_next/unit/test_documented_cli.py -v`

Expected: PASS for all `--help` forms and tiny offline config invocations.

- [ ] **Step 3: Run all new offline and Docker tests**

Run: `.venv/bin/python -m pytest tests_next/unit tests_next/integration -v`

Expected: PASS with zero failures. Do not use the legacy suite as a substitute.

- [ ] **Step 4: Re-run every individual live component command**

Run each file separately, not as one opaque invocation:

```bash
.venv/bin/python -m pytest tests_next/live/test_pi_runtime_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_task_runner_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_test_proposer_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_codex_verifier_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_check_executor_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_episode_creator_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_tree_merge_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_skillrace_proposal_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_verigrey_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_skill_generation_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_patcher_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_exact_replay_live.py --live -v -s
```

Expected: each has its own PASS and evidence directory. A full pipeline pass cannot excuse
a missing component PASS.

- [ ] **Step 5: Run the final bounded dual-model gate**

Run: `.venv/bin/python -m pytest tests_next/live/test_dual_model_gate_live.py --live -v -s`

Expected: fresh direct/Pi preflights followed by the bounded tiny Part I and Part II
slices for `deepseek-v3.2` and `glm-4.7`, with no model substitution and complete usage
evidence. Persistent provider failure produces a blocked receipt and stops the gate.

- [ ] **Step 6: Review for forbidden architecture**

Run:

```bash
rg -n "campaign_engine|parallel_campaign|adaptive_artifacts|compile_checks|check_properties|repair_validation|rq3_pipeline|event.?sourc|workflow.?engine|registry|factory" skillrace_next tests_next
```

Expected: no legacy imports or newly introduced framework machinery; legitimate prose in
tests must be manually reviewed.

- [ ] **Step 7: Commit the completed new package without performing cutover**

```bash
git add skillrace_next/cli.py skillrace_next/README.md tests_next/unit/test_documented_cli.py tests_next/live/test_dual_model_gate_live.py
git commit -m "feat(next): complete bounded clean-room pipeline"
```

Do not rename `skillrace_next`, move the legacy package, or edit canonical old docs in
this task. Present the complete evidence and diff to the user. Cutover requires a separate
explicit approval because the existing worktree is heavily modified.

## Final implementation-agent checklist

Before reporting completion, verify all of the following with fresh commands:

- [ ] `git status --short` shows no unrelated files added to your commits.
- [ ] `skillrace_next` has no import of `skillrace`.
- [ ] `.venv/bin/python -m pytest tests_next/unit tests_next/integration -v` has zero
  failures.
- [ ] Every individual live contract file has a fresh PASS and named evidence directory.
- [ ] The first live proposer, episode, merger, skill, patch, and verifier outputs received
  semantic review rather than schema-only acceptance.
- [ ] Tiny Part I proves immutable `S0`, group-before-patch, and separate discovery/repair
  metrics.
- [ ] Tiny Part II proves an accepted cumulative version chain and held-out isolation.
- [ ] Both final model preflights and bounded end-to-end gates are recorded.
- [ ] Codex never received Docker access and the Pi patcher never changed the artifact.
- [ ] No component was marked complete from an offline or mocked test alone.
- [ ] No generalized orchestration, compatibility, recovery, scheduling, or plugin layer
  was added.

When a live gate is blocked by the provider, report the exact sanitized evidence and stop
at that task. Do not continue building dependent components and do not reinterpret the
blocked gate as a pass.
