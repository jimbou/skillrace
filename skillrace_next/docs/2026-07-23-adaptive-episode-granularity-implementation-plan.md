# Adaptive Episode Granularity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce 8–20 concrete mutation-ready episodes for substantial traces and reject tree merges that require vague or inaccurate shared purposes.

**Architecture:** Keep the existing direct Pi segmentation and sequential tree fold. Change only the soft episode-count function, splitter instructions/example, and the three existing alignment judgments; purpose broadening becomes an explicit merge-admission decision.

**Tech Stack:** Python dataclasses/functions, JSON, pytest, Pi, Lab models.

---

### Task 1: Adaptive episode targets and descriptive segmentation

**Files:**
- Modify: `tests_next/unit/test_episode_creator.py`
- Modify: `skillrace_next/methods/episodes.py`
- Modify: `skillrace_next/methods/episode_assets/example_input.txt`
- Modify: `skillrace_next/methods/episode_assets/example_output.json`

- [ ] **Step 1: Write the failing target and prompt tests**

Add focused assertions:

```python
@pytest.mark.parametrize(
    ("calls", "target"),
    [(1, 1), (5, 5), (8, 8), (9, 9), (20, 10), (50, 14), (100, 20), (200, 20)],
)
def test_target_episode_count_is_adaptive(calls: int, target: int) -> None:
    assert target_episode_count(calls) == target
```

Extend the prompt test to require the concrete-description rules and verify the
worked example has ten episodes with contiguous spans beginning at calls
`1, 3, 5, 7, 8, 10, 12, 14, 15, 18`.

- [ ] **Step 2: Run the focused tests and confirm the old behavior fails**

Run:

```bash
PYTHONPATH=. pytest -q \
  tests_next/unit/test_episode_creator.py::test_target_episode_count_is_adaptive \
  tests_next/unit/test_episode_creator.py::test_create_episodes_uses_target_example_temperature_zero_and_evidence
```

Expected: the 9-, 20-, 50-, and 100-call target assertions fail, and the new prompt
wording/example assertion fails.

- [ ] **Step 3: Implement the minimum adaptive target**

Replace the existing divisor formula with:

```python
from math import ceil

def target_episode_count(tool_call_count: int) -> int:
    if isinstance(tool_call_count, bool) or not isinstance(tool_call_count, int):
        raise TypeError("tool call count must be an integer")
    if tool_call_count < 0:
        raise ValueError("tool call count must not be negative")
    if tool_call_count == 0:
        return 0
    return min(tool_call_count, min(20, 8 + ceil(max(0, tool_call_count - 8) / 8)))
```

Update `_episode_prompt` to require one concrete hypothesis, repair, or validation
objective per episode; exact files/symbols/commands and observed failures; a boundary
after each new failure or changed plan; and internal rewriting of generic lifecycle
descriptions before returning JSON.

Replace the six-episode worked output with the ten valid spans listed in Step 1.
Each record must name the exact dependency, missing file, conversion, or validation
being attempted and the observed result.

- [ ] **Step 4: Run the episode unit tests**

Run:

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_episode_creator.py
```

Expected: all episode-creator unit tests pass.

- [ ] **Step 5: Commit Task 1 only**

```bash
git add \
  tests_next/unit/test_episode_creator.py \
  skillrace_next/methods/episodes.py \
  skillrace_next/methods/episode_assets/example_input.txt \
  skillrace_next/methods/episode_assets/example_output.json
git commit -m "feat: make episode segmentation mutation-ready"
```

### Task 2: Conservative tree merge admission

**Files:**
- Modify: `tests_next/unit/test_tree_merge.py`
- Modify: `skillrace_next/methods/reasoning_tree.py`

- [ ] **Step 1: Write failing merge-admission tests**

Add one test where same-purpose returns true but broadening returns:

```json
{
  "mergeable": false,
  "purpose": null,
  "reason": "The common label would only be 'implement functionality and tests'."
}
```

Assert that the incoming episode becomes a separate root. Add a second test returning:

```json
{
  "mergeable": true,
  "purpose": "repair primitive handling in deepClone recursion",
  "reason": "Both episodes repair the same recursive primitive-handling defect."
}
```

Assert that the episodes merge and the admitted concrete purpose becomes the node
purpose. Extend instruction assertions to reject subset/superset work, generic
lifecycle matches, and generic same-approach grouping.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
PYTHONPATH=. pytest -q \
  tests_next/unit/test_tree_merge.py -k 'broad or purpose or approach'
```

Expected: failure because broaden-purpose currently accepts only `{"purpose": ...}`
and merging occurs unconditionally after same-purpose.

- [ ] **Step 3: Implement strict judgments and admission**

Change `JUDGMENT_INSTRUCTIONS` so:

- same-purpose requires the same concrete component and objective;
- subset/superset episodes and generic workflow matches return `same: false`;
- broaden-purpose returns exactly `mergeable`, `purpose`, and `reason`, using null
  purpose when admitting a merge would require generic or inaccurate broadening; and
- same-approach compares the actual technical method rather than language, skill,
  tools, or test workflow.

Include a fixed criteria string in each judgment payload so old cached judgments
cannot be reused under the new scientific rule.

Make `_broaden_purpose` return `str | None`. During child scanning, accept a child
only when both same-purpose and broaden-purpose admit it. If broadening returns
`None`, continue checking children and otherwise create a new node. Do not add the
member or increment the merged count until admission succeeds.

- [ ] **Step 4: Run all tree tests**

Run:

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_tree_merge.py
```

Expected: all tree-merge tests pass, including cache replay and validation.

- [ ] **Step 5: Commit Task 2 only**

```bash
git add tests_next/unit/test_tree_merge.py skillrace_next/methods/reasoning_tree.py
git commit -m "feat: reject overly broad episode merges"
```

### Task 3: Offline regression verification

**Files:**
- Modify only if an existing test exposes a direct regression in Task 1 or Task 2.

- [ ] **Step 1: Run all focused method and campaign tests**

```bash
PYTHONPATH=. pytest -q \
  tests_next/unit/test_episode_creator.py \
  tests_next/unit/test_tree_merge.py \
  tests_next/unit/test_campaign_commands.py \
  tests_next/unit/test_branch_view.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Run the complete offline suite**

```bash
PYTHONPATH=. pytest -q tests_next -m 'not live'
```

Expected: zero failures.

- [ ] **Step 3: Return any regression to its owning task**

This verification task should not change files or create a commit. If it exposes a
regression in episode creation, return to Task 1 and amend that focused implementation
with its tests. If it exposes a regression in tree merging, return to Task 2 and amend
that focused implementation with its tests.

### Task 4: Real-model semantic gates

**Files:**
- Modify: `tests_next/live/test_episode_creator_live.py`
- Modify: `tests_next/live/test_tree_merge_live.py`
- Evidence: `out/live-contracts/episode-creator/<model>/<run-id>/`
- Evidence: `out/live-contracts/tree-merger/<model>/<run-id>/`
- Evidence: `out/live-contracts/real-skill-episode-tree/<model>/<skill>/<run-id>/`

- [ ] **Step 1: Extend the live episode contract to use the genuine nine-call trace**

For both models, copy the immutable trace and provenance from:

```text
out/live-contracts/pilot-v4/deepseek-v4-flash/part1/js-feature/
replicates/0001/campaign/methods/skillrace/runs/0/execution/
```

Run current `create_episodes`, assert completed Lab receipts, a target of 9, complete
source coverage, and descriptive episode fields. Preserve the full evidence root.

- [ ] **Step 2: Run and inspect both episode live gates**

```bash
source ~/.bashrc
PYTHONPATH=. pytest -q -s \
  tests_next/live/test_episode_creator_live.py --live
```

Expected: DeepSeek and Qwen complete. Manually verify that the 9-call trace exposes
requirements, implementation, test construction, each discovered primitive-handling
failure, each repair, and final verification rather than three broad phases.

- [ ] **Step 3: Extend and run conservative real-skill tree gates**

Use the genuine `deepClone`, `findMissingNumber`, `toKebabCase`, and CSV traces already
copied by the live test. Assert:

- equivalent `deepClone` objectives may share a node;
- different JavaScript features remain separate roots;
- CSV creation-only does not merge with creation-plus-analysis;
- every episode member is present exactly once; and
- real judgment receipts name the requested Lab model with positive token use.

Run:

```bash
source ~/.bashrc
PYTHONPATH=. pytest -q -s \
  tests_next/live/test_tree_merge_live.py --live
```

Expected: both models pass and manual inspection confirms the tree remains concrete.

- [ ] **Step 4: Scan evidence and verify cleanup**

Confirm the active Lab key is absent from the fresh evidence and:

```bash
docker ps -a --format '{{.Names}}' | rg '^skillrace-(run|check)-'
```

Expected: no output.

- [ ] **Step 5: Commit live contract changes only**

```bash
git add \
  tests_next/live/test_episode_creator_live.py \
  tests_next/live/test_tree_merge_live.py
git commit -m "test: verify detailed real-skill episode trees"
```

- [ ] **Step 6: Record final semantic findings**

Update `skillrace_next/docs/CURRENT_STATUS.md` and
`skillrace_next/docs/FULL_STUDY_REMAINING_TODO.md` with the fresh evidence paths,
episode counts, merge structure, and any unresolved semantic limitation. Commit only
those two documents.
