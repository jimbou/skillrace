# Fair Baseline Campaigns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a defensible equal-budget campaign engine with seedless independent random generation, correctly bootstrapped VeriGrey-inspired search, SkillRACE bootstrapping, and a matched seeded no-feedback ablation.

**Architecture:** A frozen `CampaignProtocol` decides budgets and method-specific initialization. A small engine owns accounting and recovery while generators own only proposal/fold state. Adaptive generators receive explicit `bootstrap` and `explore` fold phases; random never enters a bootstrap phase. Every campaign records its exact protocol hash, attempts, counted executions, generation failures, and reconstructable generator state.

**Tech Stack:** Python 3.12 dataclasses, pytest, JSON, Docker CLI, existing generator/runner/checker modules.

---

> **Lean-protocol override:** Execute the three headline methods only. The
> `seeded-blackbox` task is deferred outside the current paper scope. The production
> protocol uses budget 30 and bootstrap count 10; see Plan 07.

## File map

- Create `skillrace/campaign_protocol.py`: validated protocol and method semantics.
- Create `skillrace/campaign_engine.py`: injectable sequential engine, accounting, resume, cleanup.
- Create `skillrace/sanity.py`: shared schema, build-state, invocability, and unsolved-task gate.
- Create `skillrace/seeded_blackbox.py`: matched no-feedback VeriGrey ablation.
- Create `experiments/protocols/pilot.json`: inexpensive development protocol.
- Create `experiments/protocols/issta-main.draft.json`: reviewable 30/10 protocol, not yet frozen.
- Create `scripts/run_experiment.py`: replication/method/skill driver with isolated outputs.
- Create `tests/test_campaign_protocol.py`: validation and method allocation.
- Create `tests/test_candidate_sanity.py`: identical pre-agent gate and no-run-on-rejection tests.
- Create `tests/test_campaign_engine.py`: equal budget, seedless random, failures, resume.
- Create `tests/test_greybox_initialization.py`: retain-all bootstrap and mutant novelty.
- Create `tests/test_seeded_blackbox.py`: no-feedback behavior and matched corpus.
- Create `tests/test_baseline_information_boundaries.py`: forbidden-information tests.
- Modify `skillrace/generator.py`: snapshot/restore and explicit independent-test provenance.
- Modify `skillrace/greybox.py`: separate initial-corpus insertion from mutant filtering; snapshot/restore.
- Modify `skillrace/loop.py`: thin CLI around protocol + campaign engine.
- Modify `scripts/run_suite.sh`: delegate to the Python driver and remove per-skill best-level selection.
- Modify `docs/design/baselines.md`, `docs/design/greybox-verigrey-adaptation.md`, `README.md`, and `docs/implementation-status.md`: align operational documentation with the approved hybrid design.

### Task 1: Encode and validate the experimental contract

**Files:**
- Create: `skillrace/campaign_protocol.py`
- Create: `tests/test_campaign_protocol.py`
- Create: `experiments/protocols/pilot.json`
- Create: `experiments/protocols/issta-main.draft.json`

- [ ] **Step 1: Write failing protocol tests**

```python
import pytest

from skillrace.campaign_protocol import CampaignProtocol


def protocol_dict():
    return {
        "schema": "campaign-protocol/1",
        "protocol_id": "test-v1",
        "status": "draft",
        "model": "qwen3.6-flash",
        "budget": 6,
        "bootstrap_count": 2,
        "max_generation_attempts_per_execution": 3,
        "seed_generator": {"batch_size": 2, "temperature": 0.9, "build_retries": 1},
        "greybox_level": "L1",
        "random_seed": 41,
    }


def test_random_is_seedless_and_adaptive_methods_bootstrap():
    protocol = CampaignProtocol.from_dict(protocol_dict())
    assert protocol.bootstrap_for("random") == 0
    assert protocol.bootstrap_for("greybox") == 2
    assert protocol.bootstrap_for("skillrace") == 2
    assert protocol.exploration_for("random") == 6
    assert protocol.exploration_for("greybox") == 4


def test_seeded_blackbox_is_subset_only():
    protocol = CampaignProtocol.from_dict(protocol_dict())
    assert protocol.bootstrap_for("seeded-blackbox") == 2
    assert "seeded-blackbox" not in protocol.headline_methods


def test_protocol_rejects_a_different_agent_model():
    data = {**protocol_dict(), "agent_model": "another-model"}
    with pytest.raises(ValueError, match="same model"):
        CampaignProtocol.from_dict(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [("budget", 0), ("bootstrap_count", -1), ("bootstrap_count", 7), ("greybox_level", "L9")],
)
def test_protocol_rejects_invalid_budget_or_level(field, value):
    data = {**protocol_dict(), field: value}
    with pytest.raises(ValueError):
        CampaignProtocol.from_dict(data)
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_campaign_protocol.py -q`

Expected: import fails because `CampaignProtocol` is absent.

- [ ] **Step 3: Implement the protocol value object**

```python
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

from .io_utils import canonical_json_hash


HEADLINE_METHODS = ("random", "greybox", "skillrace")
ADAPTIVE_METHODS = ("greybox", "skillrace", "seeded-blackbox")
ALL_METHODS = HEADLINE_METHODS + ("seeded-blackbox",)


@dataclass(frozen=True)
class CampaignProtocol:
    raw: dict
    protocol_id: str
    status: str
    model: str
    budget: int
    bootstrap_count: int
    max_generation_attempts_per_execution: int
    seed_generator: dict
    greybox_level: str
    random_seed: int

    @classmethod
    def from_dict(cls, data: dict) -> "CampaignProtocol":
        if data.get("schema") != "campaign-protocol/1":
            raise ValueError("unsupported campaign protocol schema")
        budget = int(data["budget"])
        bootstrap = int(data["bootstrap_count"])
        attempts = int(data["max_generation_attempts_per_execution"])
        level = data["greybox_level"]
        role_fields = ("agent_model", "generation_model", "segmentation_model",
                       "merge_model", "guard_model", "check_model", "revision_model")
        if any(data.get(field, data["model"]) != data["model"] for field in role_fields):
            raise ValueError("every model-driven role must use the same model")
        if budget <= 0 or bootstrap < 0 or bootstrap > budget or attempts <= 0:
            raise ValueError("invalid budget, bootstrap count, or attempt cap")
        if level not in {"L0", "L1", "L2"}:
            raise ValueError(f"invalid greybox level: {level}")
        return cls(
            raw=data,
            protocol_id=data["protocol_id"],
            status=data["status"],
            model=data["model"],
            budget=budget,
            bootstrap_count=bootstrap,
            max_generation_attempts_per_execution=attempts,
            seed_generator=dict(data["seed_generator"]),
            greybox_level=level,
            random_seed=int(data["random_seed"]),
        )

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "CampaignProtocol":
        return cls.from_dict(json.loads(pathlib.Path(path).read_text()))

    @property
    def hash(self) -> str:
        return canonical_json_hash(self.raw)

    @property
    def headline_methods(self) -> tuple[str, ...]:
        return HEADLINE_METHODS

    def bootstrap_for(self, method: str) -> int:
        if method not in ALL_METHODS:
            raise ValueError(f"unknown method: {method}")
        return self.bootstrap_count if method in ADAPTIVE_METHODS else 0

    def exploration_for(self, method: str) -> int:
        return self.budget - self.bootstrap_for(method)
```

- [ ] **Step 4: Add the two reviewable protocol files**

`experiments/protocols/pilot.json`:

```json
{
  "schema": "campaign-protocol/1",
  "protocol_id": "skillrace-pilot-v1",
  "status": "draft",
  "model": "qwen3.6-flash",
  "budget": 6,
  "bootstrap_count": 2,
  "max_generation_attempts_per_execution": 3,
  "seed_generator": {"batch_size": 2, "temperature": 0.9, "build_retries": 1},
  "greybox_level": "L1",
  "random_seed": 20260711
}
```

`experiments/protocols/issta-main.draft.json` uses the same fields with `protocol_id: "skillrace-issta-main-v1-draft"`, `budget: 30`, `bootstrap_count: 10`, `batch_size: 5`, `build_retries: 4`, and `max_generation_attempts_per_execution: 5`. It remains explicitly `draft` until Plan 6 hashes the selected model, datasets, and analysis.

The single `model` value is passed to the agent, proposer, realizer, repair, segmentation, merging, guard extraction, selection, synthesis, check compilation, and revision adapters. Remove production CLI paths that allow one role to override it independently. The model-strength ablation changes this one value for the whole pipeline.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/test_campaign_protocol.py -q`

Expected: all parametrized tests pass.

```bash
git add skillrace/campaign_protocol.py tests/test_campaign_protocol.py experiments/protocols
git commit -m "feat: encode campaign protocol"
```

### Task 2: Add the shared pre-agent candidate sanity gate

**Files:**
- Create: `skillrace/sanity.py`
- Create: `tests/test_candidate_sanity.py`
- Modify: `skillrace/generator.py:39-143,247-361`
- Modify: `skillrace/greybox.py:168-224`
- Modify: `skillrace/guards.py:263-324`
- Modify: `skillrace/seeded_blackbox.py`
- Modify: `skillrace/campaign_engine.py`

- [ ] **Step 1: Write failing schema and execution tests**

```python
from skillrace.sanity import run_candidate_sanity, validate_sanity_spec


VALID = {
    "required_paths": ["/workspace/pyproject.toml"],
    "required_tools": ["python3", "pytest"],
    "task_probe": {"command": "python3 -m pytest --collect-only -q", "allowed_exit_codes": [0, 1]},
    "unsolved_check": "python3 -m pytest -q >/dev/null 2>&1; test $? -ne 0",
}


def test_sanity_schema_accepts_explicit_mechanical_contract():
    assert validate_sanity_spec(VALID) == VALID


def test_sanity_schema_rejects_shell_without_exit_policy():
    invalid = {**VALID, "task_probe": {"command": "python3 app.py"}}
    try:
        validate_sanity_spec(invalid)
    except ValueError as error:
        assert "allowed_exit_codes" in str(error)
    else:
        raise AssertionError("task probe without exit policy was accepted")


def test_gate_checks_paths_tools_probe_and_unsolved_condition():
    seen = []

    def execute(command):
        seen.append(command)
        return 0, "ok"

    report = run_candidate_sanity("image@sha256:abc", VALID, execute=execute)
    assert report["valid"] is True
    assert [item["name"] for item in report["checks"]] == [
        "required-paths", "required-tools", "task-probe", "unsolved",
    ]
    assert len(seen) == 4


def test_failed_unsolved_check_rejects_candidate_before_agent():
    def execute(command):
        return (1, "already solved") if command == VALID["unsolved_check"] else (0, "ok")

    report = run_candidate_sanity("image@sha256:abc", VALID, execute=execute)
    assert report["valid"] is False
    assert report["rejection"] == "unsolved"
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_candidate_sanity.py -q`

Expected: import fails because `skillrace.sanity` does not exist.

- [ ] **Step 3: Implement the shared gate**

```python
from __future__ import annotations

import shlex
import subprocess


def validate_sanity_spec(spec):
    if not isinstance(spec, dict):
        raise ValueError("candidate sanity must be an object")
    paths = spec.get("required_paths")
    tools = spec.get("required_tools")
    probe = spec.get("task_probe")
    if not isinstance(paths, list) or not all(isinstance(value, str) and value for value in paths):
        raise ValueError("required_paths must be nonempty strings")
    if not isinstance(tools, list) or not all(isinstance(value, str) and value for value in tools):
        raise ValueError("required_tools must be nonempty strings")
    if not isinstance(probe, dict) or not isinstance(probe.get("command"), str):
        raise ValueError("task_probe.command is required")
    allowed = probe.get("allowed_exit_codes")
    if not isinstance(allowed, list) or not allowed or not all(isinstance(value, int) for value in allowed):
        raise ValueError("task_probe.allowed_exit_codes must be a nonempty integer list")
    unsolved = spec.get("unsolved_check")
    if unsolved is not None and not isinstance(unsolved, str):
        raise ValueError("unsolved_check must be a shell string or null")
    return spec


def docker_execute(image, command):
    process = subprocess.run(
        ["docker", "run", "--rm", image, "bash", "-lc", command],
        capture_output=True,
        text=True,
        timeout=300,
    )
    return process.returncode, (process.stdout + process.stderr)[-1000:]


def run_candidate_sanity(image, spec, execute=None):
    spec = validate_sanity_spec(spec)
    execute = execute or (lambda command: docker_execute(image, command))
    path_command = " && ".join(f"test -e {shlex.quote(path)}" for path in spec["required_paths"]) or "true"
    tool_command = " && ".join(f"command -v {shlex.quote(tool)} >/dev/null" for tool in spec["required_tools"]) or "true"
    checks = []
    for name, command, accepted in [
        ("required-paths", path_command, {0}),
        ("required-tools", tool_command, {0}),
        ("task-probe", spec["task_probe"]["command"], set(spec["task_probe"]["allowed_exit_codes"])),
    ]:
        returncode, output = execute(command)
        checks.append({"name": name, "command": command, "returncode": returncode,
                       "accepted": sorted(accepted), "output_tail": output[-300:]})
        if returncode not in accepted:
            return {"schema": "candidate-sanity/1", "valid": False,
                    "rejection": name, "checks": checks}
    unsolved = spec.get("unsolved_check")
    if unsolved is not None:
        returncode, output = execute(unsolved)
        checks.append({"name": "unsolved", "command": unsolved, "returncode": returncode,
                       "accepted": [0], "output_tail": output[-300:]})
        if returncode != 0:
            return {"schema": "candidate-sanity/1", "valid": False,
                    "rejection": "unsolved", "checks": checks}
    else:
        checks.append({"name": "unsolved", "command": None, "returncode": None,
                       "accepted": [], "output_tail": "mechanically undecidable"})
    return {"schema": "candidate-sanity/1", "valid": True,
            "rejection": None, "checks": checks}
```

- [ ] **Step 4: Extend the shared realization contract**

Change `REALIZER_SYS` and `realize` so every method receives the same model-authored, inspectable sanity record:

```json
{
  "prompt": "exact agent task",
  "tail": "Dockerfile tail",
  "sanity": {
    "required_paths": ["/workspace/path"],
    "required_tools": ["python3"],
    "task_probe": {"command": "mechanical invocation probe", "allowed_exit_codes": [0, 1]},
    "unsolved_check": "exit 0 only when the requested work remains unsolved, or null when mechanically undecidable"
  }
}
```

Return `(prompt, tail, sanity, cost)` and update Random, VeriGrey-inspired, seeded-blackbox, and SkillRACE call sites. Store `sanity` at candidate top level. The sanity prompt may use only skill context, task, environment, and built initial-state knowledge; it receives no property, trace, reasoning, guard, or tree data. SkillRACE's guard-condition `validate_sh` remains an additional method-specific validation after this shared gate.

- [ ] **Step 5: Reject invalid candidates without consuming an agent execution**

After build and check syntax but before `run_agent`, execute the shared gate and write `case/sanity.json`. A rejection is a generation failure with its reason, model/build cost, and wall time recorded. It does not call Pi or increment the agent-execution counter. Add an engine test with an agent spy that raises if called; feed a rejected sanity report and assert the spy remains untouched for random, greybox, and SkillRACE.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_candidate_sanity.py tests/test_campaign_engine.py -q
.venv/bin/python -m pytest -q
```

Expected: all sanity and offline tests pass.

```bash
git add skillrace/sanity.py skillrace/generator.py skillrace/greybox.py skillrace/guards.py skillrace/seeded_blackbox.py skillrace/campaign_engine.py tests/test_candidate_sanity.py
git commit -m "feat: share candidate sanity gate across methods"
```

### Task 3: Correct VeriGrey-inspired initial-corpus semantics

**Files:**
- Modify: `skillrace/greybox.py:107-232`
- Replace: `tests/test_pure.py:111-138`
- Create: `tests/test_greybox_initialization.py`

- [ ] **Step 1: Replace the old duplicate-seed expectation with phase-specific tests**

```python
from skillrace.greybox import GreyboxGenerator

from tests.helpers import assistant_tool, write_session


def make_generator():
    return GreyboxGenerator(
        "fix-failing-test",
        "skills/fix-failing-test",
        "skillrace/fix-failing-test:base",
    )


def test_all_initial_seeds_are_retained_even_with_duplicate_sequences():
    generator = make_generator()
    first = write_session([assistant_tool("bash", {"command": "pytest -q"})])
    second = write_session([assistant_tool("bash", {"command": "pytest -q"})])
    generator.fold_initial({"candidate_id": "a", "provenance": {}}, first)
    generator.fold_initial({"candidate_id": "b", "provenance": {}}, second)
    assert [seed["cand"]["candidate_id"] for seed in generator.corpus] == ["a", "b"]
    assert len(generator.d_seq) == 1
    assert generator.stats["initial_retained"] == 2


def test_duplicate_mutant_is_not_retained():
    generator = make_generator()
    initial = write_session([assistant_tool("bash", {"command": "pytest -q"})])
    duplicate = write_session([assistant_tool("bash", {"command": "pytest -q"})])
    generator.fold_initial({"candidate_id": "a", "provenance": {}}, initial)
    generator.fold_mutant({"candidate_id": "m", "provenance": {}}, duplicate)
    assert [seed["cand"]["candidate_id"] for seed in generator.corpus] == ["a"]
    assert generator.stats["novel_mutants"] == 0


def test_every_initial_execution_populates_coverage_database():
    generator = make_generator()
    run = write_session([
        assistant_tool("bash", {"command": "ls"}),
        assistant_tool("read", {"path": "src/a.py"}),
    ])
    generator.fold_initial({"candidate_id": "a", "provenance": {}}, run)
    assert generator.d_tool == {"bash:ls", "read:.py"}
    assert generator.d_trans == {("bash:ls", "read:.py")}
    assert generator.d_seq == {("bash:ls", "read:.py")}
```

Move `_write_session` and `_asst` from `tests/test_pure.py` into a new `tests/helpers.py` with names `write_session` and `assistant_tool`, then update old imports.

- [ ] **Step 2: Run the tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_greybox_initialization.py -q`

Expected: failure because `fold_initial` and `fold_mutant` are absent.

- [ ] **Step 3: Implement phase-specific observation and retention**

Add these methods to `GreyboxGenerator`:

```python
def _observe(self, run_dir):
    seq = schematize(run_dir, self.level)
    transitions = list(zip(seq, seq[1:]))
    energy = int(any(item not in self.d_tool for item in seq))
    energy += int(any(item not in self.d_trans for item in transitions))
    energy += int(tuple(seq) not in self.d_seq)
    self.d_tool.update(seq)
    self.d_trans.update(transitions)
    self.d_seq.add(tuple(seq))
    self.stats["folded"] += 1
    return seq, energy

def _retain(self, candidate, seq, energy):
    seed = {"cand": candidate, "seq": seq, "energy": max(1, energy)}
    self.corpus.append(seed)
    self.queue.append(seed)
    return seed

def fold_initial(self, candidate, run_dir):
    seq, energy = self._observe(run_dir)
    self.stats["initial_retained"] += 1
    return self._retain(candidate, seq, energy)

def fold_mutant(self, candidate, run_dir):
    seq, energy = self._observe(run_dir)
    if energy == 0:
        return None
    self.stats["novel_mutants"] += 1
    return self._retain(candidate, seq, energy)

def fold(self, candidate, run_dir, phase="explore"):
    if phase == "bootstrap":
        return self.fold_initial(candidate, run_dir)
    return self.fold_mutant(candidate, run_dir)
```

Initialize stats with `initial_retained`, `novel_mutants`, `folded`, `mutations`, and `skipped_builds`. Empty initial sequences are retained with energy one. An empty later sequence is retained only the first time it adds the empty tuple to sequence coverage; subsequent empty mutants are filtered as duplicates.

- [ ] **Step 4: Run the greybox tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_greybox_initialization.py tests/test_pure.py -q
```

Expected: all greybox tests pass.

- [ ] **Step 5: Commit**

```bash
git add skillrace/greybox.py tests/helpers.py tests/test_pure.py tests/test_greybox_initialization.py
git commit -m "fix: retain every greybox bootstrap seed"
```

### Task 4: Add the matched seeded no-feedback ablation

**Files:**
- Create: `skillrace/seeded_blackbox.py`
- Create: `tests/test_seeded_blackbox.py`

- [ ] **Step 1: Write failing no-feedback tests**

```python
from skillrace.seeded_blackbox import SeededBlackboxGenerator


def candidate(candidate_id):
    return {
        "candidate_id": candidate_id,
        "prompt": "fix the project",
        "provenance": {"task_nl": "fix it", "env_nl": "one failing test"},
    }


def test_initial_and_mutant_candidates_are_all_added_without_run_access(tmp_path):
    generator = SeededBlackboxGenerator.for_test(random_seed=7)
    generator.fold(candidate("s1"), tmp_path / "path-must-not-be-read", phase="bootstrap")
    generator.fold(candidate("m1"), tmp_path / "path-must-not-be-read", phase="explore")
    assert [item["candidate_id"] for item in generator.corpus] == ["s1", "m1"]


def test_seed_selection_is_uniformly_seeded_and_one_mutation_per_choice():
    generator = SeededBlackboxGenerator.for_test(random_seed=7)
    for name in ["s1", "s2", "s3"]:
        generator.fold(candidate(name), None, phase="bootstrap")
    selected = [generator.choose_seed()["candidate_id"] for _ in range(5)]
    assert selected == ["s2", "s1", "s2", "s3", "s1"]


def test_mutation_context_contains_no_tool_sequence_reasoning_or_property():
    generator = SeededBlackboxGenerator.for_test(random_seed=7)
    context = generator.mutation_context(candidate("s1"))
    assert "fix it" in context and "one failing test" in context
    for forbidden in ["TOOL SEQUENCE", "reasoning", "property", "guard", "episode"]:
        assert forbidden.lower() not in context.lower()
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_seeded_blackbox.py -q`

Expected: import fails because the generator is absent.

- [ ] **Step 3: Implement the generator using the shared realization pipeline**

Implement `SeededBlackboxGenerator` with:

- the same `skill_context`, `realize`, `repair_tail`, `containerfile_for`, and `build_image` functions used by `GreyboxGenerator`;
- a local `random.Random(random_seed)` for recorded, replayable uniform corpus selection;
- `fold(..., phase=...)` that appends every bootstrap seed and every successfully executed mutant without opening `run_dir`;
- `choose_seed()` that performs one uniform draw for each requested mutant;
- `mutation_context(candidate)` containing only skill context plus the selected candidate's `task_nl` and `env_nl`;
- a `BLACKBOX_MUTATE_SYS` prompt asking for a diverse task/environment variant without any behavioral feedback;
- provenance fields `source: "seeded-blackbox"`, `parent_candidate`, `task_nl`, `env_nl`, and `build_attempts`.

The `for_test` constructor bypasses local skill/image setup and returns an instance with an empty context, empty corpus, and seeded PRNG. The expected selection sequence in Step 1 must be verified against Python 3.12; if the standard library produces a different fixed sequence, record that exact sequence in the test and keep the seed at seven.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/test_seeded_blackbox.py -q`

Expected: all tests pass.

```bash
git add skillrace/seeded_blackbox.py tests/test_seeded_blackbox.py
git commit -m "feat: add seeded no-feedback ablation"
```

### Task 5: Add generator snapshots sufficient for deterministic recovery

**Files:**
- Modify: `skillrace/generator.py:247-361`
- Modify: `skillrace/greybox.py:107-232`
- Modify: `skillrace/seeded_blackbox.py`
- Create: `tests/test_generator_snapshots.py`

- [ ] **Step 1: Write round-trip snapshot tests**

```python
from skillrace.generator import RandomGenerator
from skillrace.greybox import GreyboxGenerator


def test_random_snapshot_restores_digest_and_buffer():
    generator = RandomGenerator.for_test(source="random")
    generator.digest = ["case one"]
    generator._buf = [{"candidate_id": "c1"}]
    restored = RandomGenerator.for_test(source="random")
    restored.restore(generator.snapshot())
    assert restored.snapshot() == generator.snapshot()


def test_greybox_snapshot_restores_novelty_corpus_queue_and_pending():
    generator = GreyboxGenerator.for_test()
    seed = {"cand": {"candidate_id": "s1"}, "seq": ["bash:pytest"], "energy": 2}
    generator.corpus = [seed]
    generator.queue.append(seed)
    generator._pending = seed
    generator.d_tool.add("bash:pytest")
    restored = GreyboxGenerator.for_test()
    restored.restore(generator.snapshot())
    assert restored.snapshot() == generator.snapshot()
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_generator_snapshots.py -q`

Expected: failure because the test constructors and snapshot methods are absent.

- [ ] **Step 3: Implement JSON-safe snapshots**

Random snapshots include `source`, `model`, `digest`, `proposed`, buffered candidate dictionaries, batch count, skip count, cost, and generator configuration. Greybox snapshots include novelty sets as sorted lists, the corpus, queue order as candidate IDs, pending candidate ID, stats, cost, level, and configuration. Restore queue and pending references by resolving IDs back to the restored corpus objects so energy mutation remains shared.

Seeded-blackbox snapshots include the corpus, stats, cost, configuration, and `random.Random.getstate()` encoded as nested JSON lists; restore converts nested lists back to tuples before `setstate()`.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/test_generator_snapshots.py -q`

Expected: both round trips pass.

```bash
git add skillrace/generator.py skillrace/greybox.py skillrace/seeded_blackbox.py tests/test_generator_snapshots.py
git commit -m "feat: snapshot generator search state"
```

### Task 6: Build an injectable equal-budget campaign engine

**Files:**
- Create: `skillrace/campaign_engine.py`
- Create: `tests/test_campaign_engine.py`
- Modify: `skillrace/loop.py`

- [ ] **Step 1: Write fake ports and the seedless/equal-budget tests**

```python
from dataclasses import dataclass

from skillrace.campaign_engine import CampaignEngine
from skillrace.campaign_protocol import CampaignProtocol


@dataclass
class FakeGenerator:
    source: str
    proposed: int = 0
    folds: list | None = None

    def __post_init__(self):
        self.folds = []

    def propose(self):
        value = {"candidate_id": f"{self.source}-{self.proposed}", "provenance": {"source": self.source}}
        self.proposed += 1
        return value

    def fold(self, candidate, run_dir, phase="explore"):
        self.folds.append((candidate["candidate_id"], phase))

    def snapshot(self):
        return {"source": self.source, "proposed": self.proposed, "folds": self.folds}


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, candidate, execution_id, attempt_id):
        self.calls.append((candidate["candidate_id"], execution_id, attempt_id))
        return {
            "agent_started": True,
            "status": "completed",
            "run_dir": f"runs/{execution_id}",
            "violated": [],
            "inconclusive": [],
            "candidate": candidate,
        }


def small_protocol():
    return CampaignProtocol.from_dict({
        "schema": "campaign-protocol/1", "protocol_id": "test", "status": "draft",
        "model": "m", "budget": 4, "bootstrap_count": 2,
        "max_generation_attempts_per_execution": 2,
        "seed_generator": {"batch_size": 1, "temperature": 0.9, "build_retries": 0},
        "greybox_level": "L1", "random_seed": 1,
    })


def test_random_has_no_bootstrap_and_uses_all_four_runs_for_fresh_tests(tmp_path):
    random_generator = FakeGenerator("random")
    forbidden_bootstrap = FakeGenerator("bootstrap")
    executor = FakeExecutor()
    state = CampaignEngine(
        protocol=small_protocol(), method="random", out_dir=tmp_path,
        generator=random_generator, bootstrap_generator=forbidden_bootstrap,
        executor=executor,
    ).run()
    assert state["counted_executions"] == 4
    assert random_generator.proposed == 4
    assert forbidden_bootstrap.proposed == 0
    assert {record[1] for record in random_generator.folds} == {"explore"}


def test_adaptive_method_spends_two_runs_on_bootstrap_and_two_on_explore(tmp_path):
    generator = FakeGenerator("greybox")
    bootstrap = FakeGenerator("bootstrap")
    state = CampaignEngine(
        protocol=small_protocol(), method="greybox", out_dir=tmp_path,
        generator=generator, bootstrap_generator=bootstrap, executor=FakeExecutor(),
    ).run()
    assert state["counted_executions"] == 4
    assert bootstrap.proposed == 2
    assert generator.proposed == 2
    assert generator.folds[:2] == [("bootstrap-0", "bootstrap"), ("bootstrap-1", "bootstrap")]
    assert all(record[1] == "explore" for record in generator.folds[2:])
```

- [ ] **Step 2: Add failure and resume tests**

Add tests where the fake executor first returns `agent_started: false` and then succeeds; assert the failed attempt is recorded but does not advance `counted_executions`. Start a budget-four campaign, deliberately stop its fake executor after two committed executions, reload the same protocol and `campaign.json`, then assert execution IDs zero and one are not repeated while IDs two and three complete. Any protocol hash mismatch must raise `ValueError`.

- [ ] **Step 3: Run the tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_campaign_engine.py -q`

Expected: import fails because `CampaignEngine` is absent.

- [ ] **Step 4: Implement the sequential engine**

The engine state schema is:

```python
def new_campaign_state(protocol, method):
    return {
        "schema": "campaign/2",
        "protocol_id": protocol.protocol_id,
        "protocol_hash": protocol.hash,
        "method": method,
        "budget": protocol.budget,
        "bootstrap_count": protocol.bootstrap_for(method),
        "counted_executions": 0,
        "attempts": [],
        "iterations": [],
        "generator_state": {},
        "complete": False,
    }
```

For execution ordinal `n`, phase is `bootstrap` only when `n < bootstrap_for(method)`. In bootstrap, call `bootstrap_generator.propose()` but fold the completed execution into the adaptive `generator`. Random always calls its main generator. Each attempt receives deterministic IDs `e{n:04d}-a{k:02d}`. Append and atomically persist an attempt record after every executor return. Append an iteration and increment `counted_executions` only when `agent_started` is true. Persist the generator snapshot after fold. Stop and mark the campaign incomplete with `stop_reason: "generation-attempt-cap"` when the per-execution cap is exhausted.

On resume, require the exact protocol hash, method, and output identity. Restore the generator snapshot. If an immutable run receipt exists without a committed iteration, finish checking/folding it before generating another candidate. The fold operation records the attempt ID in generator state and rejects a second fold of the same attempt.

Refactor `skillrace.loop` into a CLI adapter that builds real generators and an executor composed of the existing materialize, compile, run, check, and cleanup functions, then calls `CampaignEngine.run()`.

- [ ] **Step 5: Run engine and offline tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_campaign_engine.py -q
.venv/bin/python -m pytest -q
```

Expected: equal-budget, failure, resume, and complete offline suites pass.

- [ ] **Step 6: Commit**

```bash
git add skillrace/campaign_engine.py skillrace/loop.py tests/test_campaign_engine.py
git commit -m "feat: run resumable equal-budget campaigns"
```

### Task 7: Clean candidate images after their last consumer

**Files:**
- Modify: `skillrace/campaign_engine.py`
- Create: `tests/test_image_lifecycle.py`

- [ ] **Step 1: Write the lifecycle test with a spy remover**

```python
from skillrace.campaign_engine import cleanup_candidate_image


def test_candidate_image_is_removed_once_after_execution():
    removed = []
    candidate = {"built_image": "skillrace/cand-1:built"}
    cleanup_candidate_image(candidate, remover=removed.append)
    cleanup_candidate_image(candidate, remover=removed.append)
    assert removed == ["skillrace/cand-1:built"]
    assert candidate["image_cleaned"] is True


def test_missing_image_is_a_recorded_noop():
    removed = []
    candidate = {}
    cleanup_candidate_image(candidate, remover=removed.append)
    assert removed == []
```

- [ ] **Step 2: Run the test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_image_lifecycle.py -q`

Expected: import fails because the cleanup function is absent.

- [ ] **Step 3: Implement idempotent cleanup**

The default remover runs `docker image rm -f IMAGE` with captured output and no exception for an already absent image. Call cleanup in a `finally` block after check compilation and the runner have consumed the candidate. Record cleanup status/error in the attempt record. Never remove per-skill base images or the run container image before `check_properties` finishes.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_image_lifecycle.py -q`

Expected: `2 passed`.

```bash
git add skillrace/campaign_engine.py tests/test_image_lifecycle.py
git commit -m "fix: clean candidate images after use"
```

### Task 8: Prove baseline information boundaries

**Files:**
- Create: `tests/test_baseline_information_boundaries.py`
- Modify: `skillrace/generator.py`
- Modify: `skillrace/greybox.py`
- Modify: `skillrace/seeded_blackbox.py`

- [ ] **Step 1: Add poison-data tests**

Create a raw session whose assistant message contains a reasoning block with marker `SECRET_REASONING`, a tool call, and a tool result containing `SECRET_OUTCOME`. Monkeypatch the greybox model call and assert its user prompt contains only the schematized tool sequence and never either marker. Pass a properties object whose `__str__`, iteration, and indexing raise to both random and greybox constructors; proposal setup must not touch it. Give random a run directory object that raises on filesystem conversion and assert `fold` remains a no-op. Give seeded-blackbox the same poison directory and assert `fold` only appends the candidate.

Also assert no baseline candidate provenance contains `guard`, `mutation`, `targeted_property`, `episode`, or `tree_version`.

- [ ] **Step 2: Run tests and fix only the exposed access paths**

Run: `.venv/bin/python -m pytest tests/test_baseline_information_boundaries.py -q`

Expected before fixes: at least the greybox prompt test exposes any accidental non-tool context. After fixes: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add skillrace/generator.py skillrace/greybox.py skillrace/seeded_blackbox.py tests/test_baseline_information_boundaries.py
git commit -m "test: enforce baseline information boundaries"
```

### Task 9: Add the frozen-layout experiment driver

**Files:**
- Create: `scripts/run_experiment.py`
- Modify: `scripts/run_suite.sh`
- Create: `tests/test_experiment_layout.py`

- [ ] **Step 1: Write the layout test**

```python
from scripts.run_experiment import campaign_output_dir


def test_campaign_paths_isolate_protocol_replication_method_and_skill(tmp_path):
    path = campaign_output_dir(tmp_path, "pilot-v1", 3, "greybox", "fix-failing-test")
    assert path == tmp_path / "pilot-v1" / "rep-003" / "greybox" / "fix-failing-test"
```

- [ ] **Step 2: Implement the driver**

The driver accepts `--protocol`, repeatable `--skill`, repeatable `--method`, `--replications`, and `--out`. It loads the protocol once, derives each campaign RNG seed as the first 64 bits of SHA-256 over `(protocol_hash, replication, method, skill)`, and records that seed. Default methods are exactly `random`, `greybox`, and `skillrace`; `seeded-blackbox` requires an explicit `--method` and is never included in headline defaults.

Use output layout:

```text
<out>/<protocol-id>/rep-<NNN>/<method>/<skill>/campaign.json
```

For adaptive headline methods, each campaign invokes its own bootstrap generator instance using the same frozen settings but its own derived RNG seed. It does not copy or share another method's initial candidates. For the matched seeded-blackbox subset, add `--matched-initialization PATH`; both greybox and seeded-blackbox load byte-identical saved initial candidates from that recorded path.

Replace the body of `scripts/run_suite.sh` with one `exec .venv/bin/python scripts/run_experiment.py "$@"` call so there is one orchestration implementation.

- [ ] **Step 3: Remove per-skill greybox winner selection**

The driver accepts exactly one `greybox_level` from the protocol for headline runs. Add a separate `--development-granularity-sweep` mode that writes under `<out>/development-granularity/` and refuses skills present in the frozen evaluation manifest. Never run L0/L1/L2 and select the best level separately for each evaluated skill.

- [ ] **Step 4: Run tests and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_experiment_layout.py -q
.venv/bin/python scripts/run_experiment.py --help
```

Expected: layout test passes and help documents headline versus subset modes.

```bash
git add scripts/run_experiment.py scripts/run_suite.sh tests/test_experiment_layout.py
git commit -m "feat: drive isolated campaign replications"
```

### Task 10: Align documentation and run the sequential acceptance smoke

**Files:**
- Modify: `README.md`
- Modify: `docs/design/baselines.md`
- Modify: `docs/design/greybox-verigrey-adaptation.md`
- Modify: `docs/implementation-status.md`

- [ ] **Step 1: Update the operational contract**

Document all of the following verbatim in meaning:

- random generates independently for its complete budget and has no seed phase;
- greybox and SkillRACE use equal bootstrap counts from independently generated sets;
- every bootstrap run counts and can discover a defect;
- all methods have equal total exploratory agent executions;
- confirmation reruns are recorded separately as validation overhead and never alter discovery order;
- the seeded no-feedback arm is a matched subset ablation, not the headline random arm;
- the headline greybox level is frozen globally on development skills.

Delete stale claims that all methods share identical seeds or that the headline chooses a different best greybox level per evaluated skill.

- [ ] **Step 2: Run the complete offline gate**

Run:

```bash
.venv/bin/python -m pytest -q
rg -n 'identical seeds|seed phase.*every method|best level per skill' README.md docs scripts skillrace
git diff --check
```

Expected: tests pass, the search returns no stale methodological claim, and diff check is clean.

- [ ] **Step 3: Run the fake-agent smoke**

Use the campaign-engine fake executor fixture to run budget three for `random`, `greybox`, `skillrace`, and `seeded-blackbox`. Assert:

```text
random:          bootstrap=0, counted=3
greybox:         bootstrap=1, counted=3
skillrace:       bootstrap=1, counted=3
seeded-blackbox: bootstrap=1, counted=3, headline=false
```

Resume each complete campaign and assert zero additional executor calls.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/design/baselines.md docs/design/greybox-verigrey-adaptation.md docs/implementation-status.md
git commit -m "docs: align baseline methodology with protocol"
```

## Plan 2 completion gate

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/run_experiment.py --protocol experiments/protocols/pilot.json --help
git status --short
```

Expected: all offline tests pass; random cannot enter bootstrap code; duplicate adaptive seeds are retained; later duplicate greybox mutants are filtered; the no-feedback arm cannot read executions; campaign resume is exactly-once at the manifest level; and the worktree is clean.
