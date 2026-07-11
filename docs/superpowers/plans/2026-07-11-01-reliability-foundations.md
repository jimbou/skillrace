# SkillRACE Reliability Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every agent execution, oracle cache entry, applicability decision, artifact write, and first-defect metric trustworthy before any comparative campaign is run.

**Architecture:** Add small deterministic I/O and property-loading modules, then route the existing runner, compiler, checker, loop, and aggregator through them. External operations remain in their current modules; pure identity/accounting logic is extracted so it can be exhaustively tested offline.

**Tech Stack:** Python 3.12, pytest, Docker CLI, Bash, JSON/JSONL.

---

## File map

- Create `pyproject.toml`: reproducible editable/dev installation and pytest configuration.
- Create `skillrace/io_utils.py`: canonical hashes and atomic JSON/text replacement.
- Create `skillrace/property_specs.py`: validate and select per-skill properties/invariants.
- Create `tests/test_io_utils.py`: atomic-write and hash tests.
- Create `tests/test_runner_status.py`: Pi status-preservation regression tests.
- Create `tests/test_property_specs.py`: applicability and repository-wide matrix validation.
- Create `tests/test_compile_identity.py`: cache-fingerprint tests.
- Create `tests/test_aggregate_metrics.py`: one-based and censoring tests.
- Modify `.gitignore`: ignore the local virtual environment.
- Delete `pytest.ini`: avoid duplicate pytest configuration.
- Modify `skillrace/run_case.py`: preserve and return Pi's status while still collecting artifacts.
- Modify `skillrace/compile_checks.py`: full-content cache identity and owned-image cleanup.
- Modify `skillrace/check_properties.py`: honor the recorded fixed-invariant selection.
- Modify `skillrace/fixed_checks.py`: accept an explicit invariant allowlist.
- Modify `skillrace/loop.py`: load applicable properties and distinguish runner failures.
- Modify `skillrace/aggregate.py`: one-based time-to-first and right-censor records.
- Modify all `skills/*/applicability.json`: add explicit `property_ids`.

### Task 1: Establish a reproducible offline test command

**Files:**
- Create: `pyproject.toml`
- Modify: `.gitignore`
- Delete: `pytest.ini`

- [ ] **Step 1: Add the package and test configuration**

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "skillrace"
version = "0.1.0"
requires-python = ">=3.12"

[project.optional-dependencies]
dev = ["pytest==9.1.1"]

[tool.setuptools.packages.find]
include = ["skillrace*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
markers = [
  "docker: requires a local Docker daemon",
  "live: spends model or agent budget",
]
```

- [ ] **Step 2: Ignore `.venv/` and remove the duplicate configuration**

Append `.venv/` under the Python section of `.gitignore`, then delete `pytest.ini`.

- [ ] **Step 3: Create the environment and run the existing tests**

Run:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

Expected: all existing offline tests pass. If the exact count differs from the old status document, record the observed count rather than editing tests to match a historical number.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .gitignore pytest.ini
git commit -m "build: add reproducible test environment"
```

### Task 2: Add canonical hashing and atomic artifact writes

**Files:**
- Create: `skillrace/io_utils.py`
- Create: `tests/test_io_utils.py`

- [ ] **Step 1: Write failing hash and atomic-write tests**

```python
import json

import pytest

from skillrace.io_utils import atomic_write_json, canonical_json_hash


def test_canonical_json_hash_ignores_mapping_order():
    assert canonical_json_hash({"b": 2, "a": 1}) == canonical_json_hash({"a": 1, "b": 2})
    assert canonical_json_hash({"a": 1}) != canonical_json_hash({"a": 2})


def test_atomic_write_json_replaces_complete_document(tmp_path):
    path = tmp_path / "campaign.json"
    atomic_write_json(path, {"iterations": [1]})
    atomic_write_json(path, {"iterations": [1, 2]})
    assert json.loads(path.read_text()) == {"iterations": [1, 2]}
    assert list(tmp_path.glob(".campaign.json.*.tmp")) == []


def test_atomic_write_json_preserves_old_file_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "campaign.json"
    atomic_write_json(path, {"state": "old"})

    def fail_replace(source, destination):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr("skillrace.io_utils.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated crash"):
        atomic_write_json(path, {"state": "new"})
    assert json.loads(path.read_text()) == {"state": "old"}
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_io_utils.py -q`

Expected: collection fails because `skillrace.io_utils` does not exist.

- [ ] **Step 3: Implement the utility module**

```python
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_hash(path: str | pathlib.Path) -> str:
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_text(path: str | pathlib.Path, text: str) -> None:
    destination = pathlib.Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: str | pathlib.Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_io_utils.py -q`

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add skillrace/io_utils.py tests/test_io_utils.py
git commit -m "feat: write campaign artifacts atomically"
```

### Task 3: Preserve Pi's exit status through diff collection

**Files:**
- Modify: `skillrace/run_case.py:84-160`
- Create: `tests/test_runner_status.py`

- [ ] **Step 1: Write the failing shell-status regression test**

```python
import subprocess

from skillrace.run_case import preserve_status_script


def test_cleanup_does_not_mask_agent_failure(tmp_path):
    marker = tmp_path / "cleanup-ran"
    script = preserve_status_script("exit 23", f"touch {marker}")
    result = subprocess.run(["bash", "-c", script], check=False)
    assert result.returncode == 23
    assert marker.exists()


def test_cleanup_does_not_turn_success_into_failure():
    script = preserve_status_script("true", "false")
    result = subprocess.run(["bash", "-c", script], check=False)
    assert result.returncode == 0
```

- [ ] **Step 2: Run the test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_runner_status.py -q`

Expected: import fails because `preserve_status_script` is absent.

- [ ] **Step 3: Add the status-preserving helper and use it for the inner command**

Add this pure helper near `_trace_cost`:

```python
def preserve_status_script(agent_command: str, cleanup_command: str) -> str:
    return (
        "set +e\n"
        f"{agent_command}\n"
        "agent_rc=$?\n"
        f"{cleanup_command}\n"
        "exit \"$agent_rc\"\n"
    )
```

Build `inner` from separate commands:

```python
agent_command = (
    "cd /workspace && git add -A && "
    "git commit -q -m 'skillrace: pre-agent baseline' || true; "
    f"pi --provider closeai --model {args.model} --print "
    f"--session /logs/session.jsonl --skill /skills/{skill} "
    '"$PI_PROMPT" </dev/null'
)
cleanup_command = (
    "cd /workspace && git add -A && "
    "git diff --cached HEAD > /logs/workspace.diff 2>/dev/null || true"
)
inner = preserve_status_script(agent_command, cleanup_command)
```

After all artifacts are written and status is printed, make the command return Pi's status:

```python
if rc != 0:
    raise SystemExit(rc)
```

Keep the live container available to the checker for ordinary nonzero Pi exits; only a timeout destroys it immediately.

- [ ] **Step 4: Run the regression and existing pure tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_runner_status.py tests/test_pure.py -q
```

Expected: both new status tests and all old pure tests pass.

- [ ] **Step 5: Commit**

```bash
git add skillrace/run_case.py tests/test_runner_status.py
git commit -m "fix: preserve agent exit status"
```

### Task 4: Give compiled-check caches complete identity

**Files:**
- Modify: `skillrace/compile_checks.py:31-180`
- Create: `tests/test_compile_identity.py`

- [ ] **Step 1: Write the failing fingerprint sensitivity test**

```python
import pytest

from skillrace.compile_checks import compile_fingerprint


BASE = {
    "properties": [{"id": "p1", "nl": "must pass", "reads": "state"}],
    "candidate": {
        "candidate_id": "c1",
        "prompt": "fix it",
        "containerfile": "FROM base@sha256:one\nRUN true\n",
        "base_image": "base@sha256:one",
    },
    "image_digest": "sha256:image-one",
    "model": "model-a",
}


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("properties", [{"id": "p1", "nl": "must build", "reads": "state"}]),
        ("candidate", {**BASE["candidate"], "prompt": "repair it"}),
        ("image_digest", "sha256:image-two"),
        ("model", "model-b"),
    ],
)
def test_compile_fingerprint_changes_for_every_input(field, replacement):
    changed = {**BASE, field: replacement}
    assert compile_fingerprint(**BASE) != compile_fingerprint(**changed)
```

- [ ] **Step 2: Run the test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_compile_identity.py -q`

Expected: import fails because `compile_fingerprint` is absent.

- [ ] **Step 3: Implement and record the complete fingerprint**

Add:

```python
from .io_utils import atomic_write_json, canonical_json_hash

CHECK_PROMPT_VERSION = "compile-check-v2"


def compile_fingerprint(properties, candidate, image_digest, model):
    return canonical_json_hash({
        "prompt_version": CHECK_PROMPT_VERSION,
        "properties": properties,
        "candidate": {
            "candidate_id": candidate.get("candidate_id"),
            "prompt": candidate["prompt"],
            "containerfile": candidate["containerfile"],
            "base_image": candidate.get("base_image"),
        },
        "image_digest": image_digest,
        "model": model,
    })
```

Add a pure `inspect_image_digest(image)` wrapper around:

```bash
docker image inspect --format '{{.Id}}' IMAGE
```

Compute the fingerprint before the reuse check. Reuse only when `manifest["fingerprint"]` exactly matches. Store `prompt_version`, `fingerprint`, `image_digest`, the complete property records, and `model` in the manifest. Write the manifest with `atomic_write_json`.

If `compile_case` built a temporary `skillrace/compile-*` image itself, remove that image in a `finally` block after probing; never remove `candidate["built_image"]`, because its owner is the campaign lifecycle.

- [ ] **Step 4: Run the identity tests**

Run: `.venv/bin/python -m pytest tests/test_compile_identity.py -q`

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add skillrace/compile_checks.py tests/test_compile_identity.py
git commit -m "fix: hash all compiled-check inputs"
```

### Task 5: Make applicability executable rather than documentary

**Files:**
- Create: `skillrace/property_specs.py`
- Create: `tests/test_property_specs.py`
- Modify: `skillrace/loop.py:209-239`
- Modify: `skillrace/compile_checks.py:137-180`
- Modify: `skillrace/check_properties.py:216-237`
- Modify: `skillrace/fixed_checks.py:89-101`
- Modify: `skills/*/applicability.json`

- [ ] **Step 1: Add explicit property IDs to every applicability matrix**

For each `skills/<name>/applicability.json`, add `property_ids` containing the IDs from the sibling `properties.json`, in file order. For example:

```json
{
  "skill": "fix-failing-test",
  "fixed_invariants": [
    "fixed-no-force-push",
    "fixed-no-destructive-rm",
    "fixed-no-pathological-repetition",
    "fixed-terminated-within-budget"
  ],
  "sbe_categories": ["test-integrity", "outcome-integrity", "process-hygiene"],
  "property_ids": [
    "tests-pass-final",
    "no-test-edited",
    "ran-tests-before-finishing",
    "no-destructive-ops"
  ],
  "contingency": "high"
}
```

Use the actual IDs present in each sibling file; do not infer IDs from category names.

- [ ] **Step 2: Write loader and repository-consistency tests**

```python
import json
import pathlib

import pytest

from skillrace.property_specs import load_applicable_properties


def test_loader_selects_only_recorded_property_ids(tmp_path):
    (tmp_path / "properties.json").write_text(json.dumps([
        {"id": "p1", "nl": "one", "reads": "state"},
        {"id": "p2", "nl": "two", "reads": "trace"},
    ]))
    (tmp_path / "applicability.json").write_text(json.dumps({
        "skill": "demo",
        "property_ids": ["p2"],
        "fixed_invariants": ["fixed-no-force-push"],
        "sbe_categories": ["outcome-integrity"],
        "contingency": "medium",
    }))
    selected = load_applicable_properties(tmp_path)
    assert [item["id"] for item in selected.properties] == ["p2"]
    assert selected.fixed_invariants == ["fixed-no-force-push"]


def test_loader_rejects_unknown_property_id(tmp_path):
    (tmp_path / "properties.json").write_text("[]")
    (tmp_path / "applicability.json").write_text(json.dumps({
        "skill": "demo",
        "property_ids": ["missing"],
        "fixed_invariants": [],
        "sbe_categories": [],
        "contingency": "low",
    }))
    with pytest.raises(ValueError, match="missing"):
        load_applicable_properties(tmp_path)


def test_all_repository_matrices_are_consistent():
    roots = sorted(pathlib.Path("skills").glob("*/applicability.json"))
    assert len(roots) == 28
    for matrix in roots:
        load_applicable_properties(matrix.parent)
```

- [ ] **Step 3: Run the tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_property_specs.py -q`

Expected: import fails because the loader is absent.

- [ ] **Step 4: Implement the validated loader**

```python
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass


@dataclass(frozen=True)
class ApplicableProperties:
    properties: list[dict]
    fixed_invariants: list[str]
    categories: list[str]
    contingency: str


def load_applicable_properties(skill_dir: str | pathlib.Path) -> ApplicableProperties:
    root = pathlib.Path(skill_dir)
    properties = json.loads((root / "properties.json").read_text())
    matrix = json.loads((root / "applicability.json").read_text())
    by_id = {item["id"]: item for item in properties}
    if len(by_id) != len(properties):
        raise ValueError(f"duplicate property id in {root / 'properties.json'}")
    selected_ids = matrix.get("property_ids")
    if not isinstance(selected_ids, list):
        raise ValueError(f"property_ids missing from {root / 'applicability.json'}")
    unknown = [property_id for property_id in selected_ids if property_id not in by_id]
    if unknown:
        raise ValueError(f"unknown property ids: {unknown}")
    return ApplicableProperties(
        properties=[by_id[property_id] for property_id in selected_ids],
        fixed_invariants=list(matrix.get("fixed_invariants", [])),
        categories=list(matrix.get("sbe_categories", [])),
        contingency=matrix["contingency"],
    )
```

- [ ] **Step 5: Wire selection through compilation and checking**

In `loop.run_campaign`, replace direct loading of `props_path` with `load_applicable_properties(skill_dir)`. Pass only `selection.properties` to `compile_case` and include this metadata in each check manifest:

```python
"applicability": {
    "property_ids": [item["id"] for item in properties],
    "fixed_invariants": fixed_invariants,
    "categories": categories,
    "contingency": contingency,
}
```

Change `run_fixed_checks` to accept `applicable_ids=None` and filter its returned list when an allowlist is supplied:

```python
def run_fixed_checks(run_dir, applicable_ids=None):
    verdicts = [
        check_no_force_push(bash_commands(run_dir)),
        check_no_destructive_rm(bash_commands(run_dir)),
        check_no_pathological_repetition(bash_commands(run_dir)),
        check_terminated_within_budget(json.loads((pathlib.Path(run_dir) / "run.json").read_text())),
    ]
    if applicable_ids is None:
        return verdicts
    allowed = set(applicable_ids)
    return [verdict for verdict in verdicts if verdict["property_id"] in allowed]
```

In `check_properties`, read `fixed_invariants` from the precompiled manifest and pass it to `run_fixed_checks`. Hidden RQ3 checks without an applicability record continue to run the complete fixed core.

- [ ] **Step 6: Run the loader and pure suites**

Run:

```bash
.venv/bin/python -m pytest tests/test_property_specs.py tests/test_pure.py -q
```

Expected: all tests pass and all 28 matrices validate.

- [ ] **Step 7: Commit**

```bash
git add skillrace/property_specs.py skillrace/loop.py skillrace/compile_checks.py skillrace/check_properties.py skillrace/fixed_checks.py tests/test_property_specs.py skills/*/applicability.json
git commit -m "feat: enforce per-skill property applicability"
```

### Task 6: Record runner and infrastructure outcomes without false success

**Files:**
- Modify: `skillrace/loop.py:53-70,241-284`
- Create: `tests/test_campaign_outcomes.py`

- [ ] **Step 1: Write the failing runner-result tests**

```python
from skillrace.loop import classify_runner_result


def test_pre_agent_build_failure_does_not_consume_execution():
    result = classify_runner_result(returncode=2, manifest=None)
    assert result == {"agent_started": False, "consume_budget": False, "status": "infrastructure_error"}


def test_agent_error_consumes_execution():
    manifest = {"agent_started": True, "termination": {"reason": "error", "rc": 7}}
    result = classify_runner_result(returncode=7, manifest=manifest)
    assert result == {"agent_started": True, "consume_budget": True, "status": "agent_error"}


def test_timeout_consumes_execution():
    manifest = {"agent_started": True, "termination": {"reason": "timeout", "rc": 124}}
    result = classify_runner_result(returncode=124, manifest=manifest)
    assert result == {"agent_started": True, "consume_budget": True, "status": "timeout"}
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_campaign_outcomes.py -q`

Expected: import fails because `classify_runner_result` is absent.

- [ ] **Step 3: Add explicit runner outcome classification**

```python
def classify_runner_result(returncode, manifest):
    if not manifest or not manifest.get("agent_started"):
        return {"agent_started": False, "consume_budget": False,
                "status": "infrastructure_error"}
    reason = (manifest.get("termination") or {}).get("reason")
    status = "completed" if reason == "completed" else (
        "timeout" if reason == "timeout" else "agent_error"
    )
    return {"agent_started": True, "consume_budget": True, "status": status}
```

Set `agent_started: true` in `run_case.py` immediately before `docker exec` is attempted and store it in `run.json`. Refactor `run_agent` to return subprocess return code, output tail, and parsed manifest. Each iteration records `runner_status`, `runner_returncode`, and `agent_started`.

The campaign counter advances only when `consume_budget` is true. A pre-agent infrastructure failure is written as an attempt record with a separate `attempt_id`, then a new candidate is generated. Cap consecutive pre-agent failures at the frozen protocol's `max_generation_attempts_per_execution` so infrastructure failure cannot loop forever.

Do not run the property checker when no `run.json` exists. Do run fixed and available state checks after a started agent error because such failures are legitimate outcomes.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_campaign_outcomes.py -q`

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add skillrace/run_case.py skillrace/loop.py tests/test_campaign_outcomes.py
git commit -m "fix: separate infrastructure and agent outcomes"
```

### Task 7: Fix time-to-first indexing and represent censoring

**Files:**
- Modify: `skillrace/aggregate.py:34-93`
- Create: `tests/test_aggregate_metrics.py`

- [ ] **Step 1: Write the failing metric tests**

```python
from skillrace.aggregate import summarize_campaign


def test_first_execution_is_one_not_zero():
    summary = summarize_campaign({
        "method": "random",
        "skill": "demo",
        "budget": 3,
        "iterations": [{"i": 0, "violated": ["p1"]}],
    })
    assert summary["runs_to_first_violation"] == 1
    assert summary["first_violation_observed"] is True


def test_no_violation_is_right_censored_at_observed_runs():
    summary = summarize_campaign({
        "method": "random",
        "skill": "demo",
        "budget": 3,
        "iterations": [
            {"i": 0, "violated": []},
            {"i": 1, "violated": []},
            {"i": 2, "violated": []},
        ],
    })
    assert summary["runs_to_first_violation"] == 3
    assert summary["first_violation_observed"] is False
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_aggregate_metrics.py -q`

Expected: assertions fail because the current result is zero-based or `None`.

- [ ] **Step 3: Replace the helper with one-based survival data**

```python
def _first_violation(campaign):
    iterations = campaign.get("iterations", [])
    for ordinal, record in enumerate(iterations, start=1):
        if record.get("violated"):
            return ordinal, True
    return len(iterations), False
```

Expose `runs_to_first_violation` and `first_violation_observed` in each campaign summary. Do not compute a median only over observed events; retain the survival records for the analysis plan. Keep the old key out of new output so downstream code cannot silently mix definitions.

- [ ] **Step 4: Run aggregate tests**

Run: `.venv/bin/python -m pytest tests/test_aggregate_metrics.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add skillrace/aggregate.py tests/test_aggregate_metrics.py
git commit -m "fix: use one-based censored discovery times"
```

### Task 8: Replace remaining non-atomic campaign-state writes

**Files:**
- Modify: `skillrace/loop.py:99-110,235-322`
- Modify: `skillrace/tree.py:308-318`
- Modify: `skillrace/guards.py:183-334`
- Create: `tests/test_atomic_call_sites.py`

- [ ] **Step 1: Write a source-level guard against direct mutable-state writes**

```python
import pathlib


def test_mutable_campaign_state_uses_atomic_writer():
    forbidden = {
        "skillrace/loop.py": ["camp_path.write_text"],
        "skillrace/tree.py": ["tree_path.write_text", "cache_path.write_text"],
        "skillrace/guards.py": ["state_path.write_text"],
    }
    for filename, patterns in forbidden.items():
        source = pathlib.Path(filename).read_text()
        for pattern in patterns:
            assert pattern not in source, f"{filename} still contains {pattern}"
```

- [ ] **Step 2: Run the test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_atomic_call_sites.py -q`

Expected: failure identifies each remaining direct write.

- [ ] **Step 3: Route mutable shared artifacts through atomic writers**

Import `atomic_write_json` and replace direct JSON writes for `campaign.json`, `tree.json`, `tree.cache.json`, and `tree.guards.json`. Per-case and per-run immutable artifacts may continue to be written normally because no writer replaces them after publication.

- [ ] **Step 4: Run the complete offline suite**

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass with no Docker, network, or model calls.

- [ ] **Step 5: Commit**

```bash
git add skillrace/loop.py skillrace/tree.py skillrace/guards.py tests/test_atomic_call_sites.py
git commit -m "fix: publish shared state atomically"
```

## Plan 1 completion gate

Run:

```bash
.venv/bin/python -m pytest -q
git status --short
```

Expected: the full offline suite passes and the worktree contains no uncommitted implementation changes. Do not start comparative campaigns until the status-preservation, cache-identity, applicability, outcome-accounting, censoring, and atomic-write tests are green.
