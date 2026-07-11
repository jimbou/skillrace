# Parallel SkillRACE Campaigns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce campaign wall time without corrupting adaptive search state, while preserving every opportunistic test and recording whether it reached its motivating branch.

**Architecture:** Workers receive immutable jobs and write immutable result directories. One reducer owns each campaign manifest and adaptive state. Random and greybox reserve proposals in batches; SkillRACE freezes tree version N, creates a diverse target batch, runs it concurrently, and folds completed results in stable candidate-ID order into version N+1.

**Tech Stack:** Python 3.12, `concurrent.futures`, threading semaphores, pytest, Docker, existing model and runner subprocesses.

---

## File map

- Create `skillrace/resource_pool.py`: explicit API, build, and agent semaphores.
- Create `skillrace/parallel_campaign.py`: immutable jobs/results, epoch planner, deterministic reducer.
- Create `skillrace/ablations.py`: explicit, testable mechanism substitutions for the subset study.
- Create `tests/test_resource_pool.py`: concurrency limits.
- Create `tests/test_parallel_campaign.py`: worker isolation and fold order.
- Create `tests/test_ablations.py`: uniform, outcomes-only, and direct-property information-flow tests.
- Create `tests/test_skillrace_classification.py`: intended, incidental, no-divergence, and path-miss cases.
- Modify `skillrace/generator.py`: caller-supplied stable candidate IDs and batch proposal.
- Modify `skillrace/greybox.py`: reserve mutation work from a frozen corpus snapshot.
- Modify `skillrace/guards.py`: diverse target batches and opportunistic mutation prompt.
- Modify `skillrace/loop.py`: richer target provenance and four-way classification.
- Modify `skillrace/campaign_engine.py`: opt-in epoch execution through the deterministic reducer.
- Modify `scripts/run_experiment.py`: global resource pool across method/skill/replication tasks.

### Task 1: Use stable proposal and candidate identities

**Files:**
- Modify: `skillrace/generator.py`
- Modify: `skillrace/greybox.py`
- Modify: `skillrace/guards.py`
- Create: `tests/test_candidate_identity.py`

- [ ] **Step 1: Write the failing identity tests**

```python
from skillrace.parallel_campaign import candidate_id


def test_candidate_id_is_stable_and_scope_sensitive():
    first = candidate_id("protocol/rep-001/random/demo", "e0004", "a01", 0)
    assert first == candidate_id("protocol/rep-001/random/demo", "e0004", "a01", 0)
    assert first != candidate_id("protocol/rep-001/random/demo", "e0004", "a01", 1)
    assert first != candidate_id("protocol/rep-001/greybox/demo", "e0004", "a01", 0)
    assert first.startswith("cand-") and len(first) == 21
```

- [ ] **Step 2: Run the test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_candidate_identity.py -q`

Expected: import fails because the identity helper is absent.

- [ ] **Step 3: Implement stable IDs and thread them through generators**

```python
import hashlib


def candidate_id(campaign_id: str, execution_id: str, attempt_id: str, slot: int) -> str:
    payload = f"{campaign_id}\0{execution_id}\0{attempt_id}\0{slot}".encode()
    return "cand-" + hashlib.sha256(payload).hexdigest()[:16]
```

Change generator entry points to accept `proposal_id`; use it instead of `uuid` when supplied. Production campaign code always supplies it, while standalone generator CLIs may retain UUID fallback. Store `proposal_id`, `campaign_id`, `execution_id`, `attempt_id`, and `epoch` in provenance.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_candidate_identity.py -q`

Expected: test passes.

```bash
git add skillrace/generator.py skillrace/greybox.py skillrace/guards.py skillrace/parallel_campaign.py tests/test_candidate_identity.py
git commit -m "feat: assign stable campaign candidate ids"
```

### Task 2: Enforce global resource limits

**Files:**
- Create: `skillrace/resource_pool.py`
- Create: `tests/test_resource_pool.py`

- [ ] **Step 1: Write semaphore accounting tests**

```python
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from skillrace.resource_pool import ResourcePool


def test_agent_limit_is_never_exceeded():
    pool = ResourcePool(api=4, docker=4, agent=2)
    lock = threading.Lock()
    active = 0
    peak = 0

    def work():
        nonlocal active, peak
        with pool.agent_slot():
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: work(), range(12)))
    assert peak == 2


def test_invalid_limit_is_rejected():
    try:
        ResourcePool(api=0, docker=1, agent=1)
    except ValueError as error:
        assert "positive" in str(error)
    else:
        raise AssertionError("zero API limit was accepted")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_resource_pool.py -q`

Expected: import fails.

- [ ] **Step 3: Implement named bounded semaphores**

```python
from __future__ import annotations

import contextlib
import threading


class ResourcePool:
    def __init__(self, api: int, docker: int, agent: int):
        if min(api, docker, agent) <= 0:
            raise ValueError("resource limits must be positive")
        self._api = threading.BoundedSemaphore(api)
        self._docker = threading.BoundedSemaphore(docker)
        self._agent = threading.BoundedSemaphore(agent)

    @contextlib.contextmanager
    def _slot(self, semaphore):
        semaphore.acquire()
        try:
            yield
        finally:
            semaphore.release()

    def api_slot(self):
        return self._slot(self._api)

    def docker_slot(self):
        return self._slot(self._docker)

    def agent_slot(self):
        return self._slot(self._agent)
```

Wrap every model call, Docker build/probe, and Pi execution at the adapter boundary. Nested work must acquire at most one named slot at a time to avoid lock-order deadlocks.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_resource_pool.py -q`

Expected: `2 passed`.

```bash
git add skillrace/resource_pool.py tests/test_resource_pool.py
git commit -m "feat: bound campaign resources"
```

### Task 3: Make workers immutable and reducer-owned

**Files:**
- Create: `skillrace/parallel_campaign.py`
- Create: `tests/test_parallel_campaign.py`

- [ ] **Step 1: Write worker isolation tests**

```python
import json

from skillrace.parallel_campaign import ParallelReducer, WorkerJob, run_worker


def test_worker_writes_only_its_result_directory(tmp_path):
    shared = tmp_path / "campaign.json"
    shared.write_text('{"sentinel":true}\n')
    job = WorkerJob(
        candidate={"candidate_id": "cand-a"},
        phase="explore",
        epoch=2,
        result_dir=tmp_path / "workers" / "cand-a",
    )
    result = run_worker(job, executor=lambda value: {"agent_started": True, "run_dir": "runs/a"})
    assert json.loads(shared.read_text()) == {"sentinel": True}
    assert (job.result_dir / "receipt.json").exists()
    assert result.candidate_id == "cand-a"


def test_reducer_folds_in_candidate_id_order(tmp_path):
    folded = []
    reducer = ParallelReducer(fold=lambda result: folded.append(result.candidate_id))
    results = [
        type("R", (), {"candidate_id": "cand-z"})(),
        type("R", (), {"candidate_id": "cand-a"})(),
        type("R", (), {"candidate_id": "cand-m"})(),
    ]
    reducer.reduce(results)
    assert folded == ["cand-a", "cand-m", "cand-z"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_parallel_campaign.py -q`

Expected: missing types/functions.

- [ ] **Step 3: Implement job/result contracts and deterministic reduction**

`WorkerJob` is a frozen dataclass containing candidate, phase, epoch, and result directory. `WorkerResult` is a frozen dataclass containing candidate ID, phase, epoch, agent-started flag, runner/check outcome, run directory, violated/inconclusive IDs, costs, timings, and infrastructure errors. `run_worker` creates its result directory with `exist_ok=False`, invokes the supplied executor, writes one atomic `receipt.json`, and never receives a campaign, tree, guard-state, or coverage path.

`ParallelReducer.reduce` sorts by candidate ID and invokes its fold callback serially. It alone appends campaign iterations, updates generator state, writes tree/coverage state, and atomically replaces the campaign manifest.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_parallel_campaign.py -q`

Expected: both tests pass.

```bash
git add skillrace/parallel_campaign.py tests/test_parallel_campaign.py
git commit -m "feat: isolate parallel campaign workers"
```

### Task 4: Plan bounded epochs from frozen adaptive state

**Files:**
- Modify: `skillrace/parallel_campaign.py`
- Modify: `skillrace/greybox.py`
- Modify: `skillrace/guards.py`
- Create: `tests/test_epoch_planning.py`

- [ ] **Step 1: Write frozen-version and diversity tests**

```python
from skillrace.parallel_campaign import plan_epoch


def test_skillrace_epoch_uses_one_frozen_tree_version_and_unique_targets():
    targets = [
        {"branch_key": "b1", "mutation": "m1"},
        {"branch_key": "b1", "mutation": "m2"},
        {"branch_key": "b2", "mutation": "m1"},
    ]
    jobs = plan_epoch("skillrace", targets, epoch=4, tree_version=9, limit=3)
    assert {job["tree_version"] for job in jobs} == {9}
    assert len({(job["branch_key"], job["mutation"]) for job in jobs}) == 3


def test_epoch_never_exceeds_remaining_budget():
    jobs = plan_epoch("random", [{"slot": value} for value in range(8)], epoch=1,
                      tree_version=None, limit=2)
    assert len(jobs) == 2
```

- [ ] **Step 2: Implement reservation without folding**

For random, reserve fresh proposal IDs. For greybox, clone the scheduler snapshot, reserve seed/energy choices sequentially from that clone, then generate and execute the reserved mutations independently; completed mutants are folded only by the reducer. For SkillRACE, freeze `tree.json`, tree cache, and guard state under version N; rank the frontier once; choose up to `epoch_size` distinct `(branch_key, mutation)` pairs; synthesize them concurrently; and mark mutations tried only when synthesis yields a valid candidate or a terminal synthesis failure receipt.

The default epoch size is four and is bounded by remaining budget, agent slots, and available distinct targets. If the frontier has fewer targets, fill remaining slots with recorded SkillRACE fallbacks; fallbacks are still folded into the tree.

- [ ] **Step 3: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_epoch_planning.py -q`

Expected: tests pass.

```bash
git add skillrace/parallel_campaign.py skillrace/greybox.py skillrace/guards.py tests/test_epoch_planning.py
git commit -m "feat: plan bounded adaptive epochs"
```

### Task 5: Record opportunistic mutation provenance and four-way outcomes

**Files:**
- Modify: `skillrace/guards.py:74-100,226-326`
- Modify: `skillrace/loop.py:113-188,241-280`
- Create: `tests/test_skillrace_classification.py`

- [ ] **Step 1: Write classification tests**

```python
from skillrace.loop import classify_target_execution


def test_intended_branch_reached_and_new_child_created():
    actions = [("merge", "n0", ""), ("merge", "n1", ""), ("new", "n9", "")]
    assert classify_target_execution(actions, "n1") == "intended_branch"


def test_different_new_branch_is_kept():
    actions = [("merge", "n0", ""), ("new", "n8", "")]
    assert classify_target_execution(actions, "n4") == "different_new_branch"


def test_reached_target_without_new_behavior():
    actions = [("merge", "n0", ""), ("merge", "n1", ""), ("merge", "n2", "")]
    assert classify_target_execution(actions, "n1") == "no_divergence"


def test_path_miss_has_no_incidental_new_node():
    actions = [("merge", "n0", "")]
    assert classify_target_execution(actions, "n4") == "path_miss"


def test_virtual_root_target_uses_first_action():
    assert classify_target_execution([("new", "n1", "")], None) == "intended_branch"
    assert classify_target_execution([("merge", "n1", "")], None) == "no_divergence"
```

- [ ] **Step 2: Implement classification and discovery relationship**

```python
def classify_target_execution(actions, target_parent):
    if actions is None:
        return "unfolded"
    if target_parent is None:
        return "intended_branch" if actions and actions[0][0] == "new" else "no_divergence"
    node_ids = [node_id for _, node_id, _ in actions]
    if target_parent in node_ids:
        position = node_ids.index(target_parent)
        if position + 1 < len(actions) and actions[position + 1][0] == "new":
            return "intended_branch"
        return "no_divergence"
    if any(kind == "new" for kind, _, _ in actions):
        return "different_new_branch"
    return "path_miss"
```

Each SkillRACE candidate records `guard`, `branch_key`, `target_parent`, `mutation`, `targeted_property`, `validation`, `tree_version`, and `epoch`. After checking, each violation is marked `targeted` only when its property ID equals `targeted_property`; every other violation is `serendipitous`. Both kinds count equally toward confirmed yield.

Change `SYNTH_SYS` to say that the test may alter multiple coherent environment features when useful, need not minimally isolate the stated guard, and remains valuable if it exposes another branch or defect. Keep the executable condition validation requirement unchanged.

- [ ] **Step 3: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_skillrace_classification.py -q`

Expected: `5 passed`.

```bash
git add skillrace/guards.py skillrace/loop.py tests/test_skillrace_classification.py
git commit -m "feat: record opportunistic branch outcomes"
```

### Task 6: Implement mechanism ablations as explicit strategies

**Files:**
- Create: `skillrace/ablations.py`
- Create: `tests/test_ablations.py`
- Modify: `skillrace/guards.py`
- Modify: `skillrace/campaign_engine.py`
- Modify: `scripts/run_experiment.py`

- [ ] **Step 1: Write failing strategy and information-flow tests**

```python
from skillrace.ablations import AblationConfig, choose_frontier_item, guard_view


def test_uniform_frontier_uses_recorded_rng_not_property_ranker():
    frontier = [{"branch_key": "b1"}, {"branch_key": "b2"}, {"branch_key": "b3"}]
    selected = choose_frontier_item(frontier, policy="uniform", random_seed=7)
    assert selected == {"branch_key": "b2"}


def test_outcomes_only_view_removes_all_reasoning_text():
    branch = {
        "condition": "tests failed",
        "sides": [{"outcome": "pytest exit 1", "opening_reasoning": "SECRET_REASONING"}],
    }
    view = guard_view(branch, signal_mode="outcomes-only")
    assert view == {"condition": "tests failed", "sides": [{"outcome": "pytest exit 1"}]}
    assert "SECRET_REASONING" not in str(view)


def test_headline_rejects_nonfull_ablation():
    config = AblationConfig(name="uniform-frontier", frontier_policy="uniform",
                            signal_mode="reasoning-and-outcomes", generator="tree")
    try:
        config.validate(headline=True)
    except ValueError as error:
        assert "headline" in str(error)
    else:
        raise AssertionError("ablation entered headline comparison")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_ablations.py -q`

Expected: import fails because `skillrace.ablations` is absent.

- [ ] **Step 3: Implement four frozen strategy configurations**

```python
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class AblationConfig:
    name: str
    frontier_policy: str
    signal_mode: str
    generator: str

    def validate(self, headline=False):
        if self.frontier_policy not in {"property-guided", "uniform", "none"}:
            raise ValueError("invalid frontier policy")
        if self.signal_mode not in {"reasoning-and-outcomes", "outcomes-only", "none"}:
            raise ValueError("invalid signal mode")
        if self.generator not in {"tree", "direct-property"}:
            raise ValueError("invalid generator")
        if headline and self.name != "full":
            raise ValueError("only full SkillRACE is allowed in the headline")
        return self


ABLATIONS = {
    "full": AblationConfig("full", "property-guided", "reasoning-and-outcomes", "tree"),
    "uniform-frontier": AblationConfig("uniform-frontier", "uniform", "reasoning-and-outcomes", "tree"),
    "outcomes-only": AblationConfig("outcomes-only", "property-guided", "outcomes-only", "tree"),
    "direct-property": AblationConfig("direct-property", "none", "none", "direct-property"),
}


def choose_frontier_item(frontier, policy, random_seed):
    if policy != "uniform":
        raise ValueError("uniform helper received another policy")
    return random.Random(random_seed).choice(frontier)


def guard_view(branch, signal_mode):
    if signal_mode == "reasoning-and-outcomes":
        return branch
    if signal_mode != "outcomes-only":
        raise ValueError("guard view requires tree signals")
    return {
        "condition": branch["condition"],
        "sides": [{"outcome": side.get("outcome", "")} for side in branch.get("sides", [])],
    }
```

- [ ] **Step 4: Wire each strategy without changing shared execution**

Uniform selection chooses from the same feasible frontier with a recorded derived RNG seed and never calls the property ranker. Outcomes-only extraction removes opening reasoning before any guard prompt or cache key is built. Direct-property generation receives only skill context, applicable property text, and the random diversity digest; it uses the shared realization, repair, sanity, runner, and checker but never builds episodes or a tree. The seeded no-feedback and model-strength arms remain implemented by Plan 2 and the protocol-level whole-pipeline model swap respectively.

Add `--ablation` to `scripts/run_experiment.py`, default `full`. Non-full choices require `--subset-manifest experiments/manifests/ablations.json` and write outside headline paths. Record the complete `AblationConfig` and its hash in every campaign.

- [ ] **Step 5: Run and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_ablations.py -q
.venv/bin/python -m pytest -q
```

Expected: ablation and full offline suites pass.

```bash
git add skillrace/ablations.py skillrace/guards.py skillrace/campaign_engine.py scripts/run_experiment.py tests/test_ablations.py
git commit -m "feat: implement SkillRACE mechanism ablations"
```

### Task 7: Run campaigns and epochs concurrently under one reducer per campaign

**Files:**
- Modify: `skillrace/campaign_engine.py`
- Modify: `scripts/run_experiment.py`
- Create: `tests/test_parallel_driver.py`

- [ ] **Step 1: Write a global-limit driver test**

Use fake campaign callables that block on a barrier and record active counts. Configure campaign workers four and agent slots two. Assert multiple skills/methods overlap while the shared agent peak remains two. Assert two reducers never receive the same output directory.

- [ ] **Step 2: Integrate epoch execution**

`CampaignEngine` accepts `epoch_size` and `ResourcePool`. It plans at most the remaining counted budget, submits worker jobs to a thread pool, waits for the entire epoch, and reduces receipts deterministically. An interrupted epoch is resumable from immutable receipts. Random and greybox may use larger epochs because they do not own a tree; SkillRACE defaults to four so feedback is refreshed frequently.

`scripts/run_experiment.py` creates one global `ResourcePool` and one campaign thread pool. Skills, methods, replications, and model-ablation cells can overlap, but each output directory is submitted once. Write a top-level `schedule.json` recording queued, running, completed, and failed cells atomically.

- [ ] **Step 3: Run offline tests and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_parallel_driver.py tests/test_parallel_campaign.py tests/test_resource_pool.py -q
.venv/bin/python -m pytest -q
```

Expected: all tests pass without live Docker/model calls.

```bash
git add skillrace/campaign_engine.py scripts/run_experiment.py tests/test_parallel_driver.py
git commit -m "feat: execute campaigns in bounded parallel epochs"
```

### Task 8: Verify deterministic reduction on replayed traces

**Files:**
- Create: `tests/integration/test_epoch_replay.py`
- Create: `tests/fixtures/epoch-replay/`

- [ ] **Step 1: Add four recorded candidates and Pi traces**

Use fixtures containing two target-reaching traces, one incidental-new-branch trace, and one path miss. Store recorded segment/merge model responses so the test has no API calls.

- [ ] **Step 2: Run the same epoch under two completion orders**

The fake executor returns receipts in `a,b,c,d` order once and `d,b,a,c` order once. Reduce both into fresh directories. Assert byte-identical normalized `tree.json`, `tree.guards.json`, generator snapshot, campaign iterations, and classifications after removing timestamps and wall-clock fields.

- [ ] **Step 3: Run the integration gate**

Run:

```bash
.venv/bin/python -m pytest tests/integration/test_epoch_replay.py -q
git diff --check
```

Expected: replay passes and the diff is clean.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_epoch_replay.py tests/fixtures/epoch-replay
git commit -m "test: prove deterministic epoch reduction"
```

## Plan 3 completion gate

Run the full offline suite, then one live two-epoch pilot with agent concurrency two. Confirm from manifests that worker completion order differs from fold order, all valid runs are retained, no tree or campaign file has multiple writers, resource peaks respect limits, and defect counts do not depend on intended branch reach.
