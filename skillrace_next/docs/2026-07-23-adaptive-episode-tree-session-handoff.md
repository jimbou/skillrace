# Adaptive Episode/Tree Session Handoff

Date: 2026-07-23  
Branch: `skillrace-next-clean-room`

## Objective

Finish and verify the adaptive, mutation-ready episode splitter and conservative tree
merger before the full SkillRACE study.

The scientific decisions are settled:

- episodes represent concrete reasoning attempts, not broad phases;
- substantial traces should produce approximately 8–20 episodes;
- short traces should approach one episode per reasoning-bearing tool call;
- descriptions must name the exact component, decision, bug, repair, command, and
  observed evidence;
- outcomes may use only tool results inside their own episode span;
- episodes merge only when they share a concrete technical purpose;
- if a truthful shared purpose would become generic or would add work absent from a
  member, the merge is rejected; and
- identical concrete work may still merge. For example, different JavaScript tasks
  may share an exact "read the same js-feature skill" episode, but their
  `deepClone`, `findMissingNumber`, and `toKebabCase` implementation episodes must
  remain distinct.

Read these first:

1. `skillrace_next/docs/2026-07-23-adaptive-episode-granularity-design.md`
2. `skillrace_next/docs/2026-07-23-adaptive-episode-granularity-implementation-plan.md`

Task 4 of the implementation plan is still in progress.

## Completed and committed

The following commits belong to this change:

- `7f2f407` — initial adaptive-granularity design;
- `9fc0b47` — descriptive episodes and conservative merge requirements;
- `95c754b` — task-by-task implementation plan;
- `2ceaf7d` — adaptive target, detailed prompt, and ten-episode example;
- `a32baf7` — conservative merge admission;
- `7aa134a` — outcomes grounded inside their episode spans; and
- `aa5ee6d` — explicit correction diagnostic for Markdown-fenced responses.

Implemented behavior:

```text
target(N) = min(N, min(20, 8 + ceil(max(0, N - 8) / 8)))
```

Examples: 5 calls → 5 episodes, 9 → 9, 20 → 10, 50 → 14,
100+ → at most 20.

`skillrace_next/methods/reasoning_tree.py` now:

- uses strict concrete-purpose and concrete-approach prompts;
- gives each judgment payload a `*-v2` criteria value so old cache entries do not
  silently apply;
- makes broaden-purpose return `mergeable`, `purpose`, and `reason`;
- continues searching or creates a node when broadening rejects a merge; and
- adds membership only after admission succeeds.

## Offline verification

Fresh results from this session:

- focused episode/tree/campaign/edge-selector suite: 59 passed;
- complete offline suite: 255 passed;
- focused episode suite after the grounding/fence diagnostics: 25 passed.

Commands:

```bash
PYTHONPATH=. pytest -q \
  tests_next/unit/test_episode_creator.py \
  tests_next/unit/test_tree_merge.py \
  tests_next/unit/test_campaign_commands.py \
  tests_next/unit/test_edge_selector.py

PYTHONPATH=. pytest -q tests_next -m 'not live'
```

The implementation commits are clean. The unrelated untracked
`skillrace_next/study/base-images/manifest.json` predates this work; do not add,
delete, clean, or modify it.

## Real-model evidence and semantic findings

Both models have produced a successful 9-call → 9-episode segmentation of the genuine
`js-feature`/`deepClone` execution.

DeepSeek current successful evidence:

```text
out/live-contracts/episode-creator/deepseek-v4-flash/
20260723T095812Z-68e51880/
```

Qwen successful isolated evidence:

```text
out/live-contracts/episode-creator/qwen3.6-flash/
20260723T095315Z-5b04a294/
```

The successful output contains separate episodes for:

1. reading the `js-feature` workflow;
2. writing `deepClone.js`;
3. writing `test.js`;
4. observing the first primitive-object-property `TypeError`;
5. applying the first primitive guard;
6. observing the remaining primitive-array-item failure;
7. applying the array guard;
8. confirming all eight agent tests pass; and
9. inspecting the final implementation.

This is the intended granularity. The earlier three-phase split is no longer
acceptable evidence.

## Current blocker

The required full episode live suite ended with five passes and one Qwen provider
failure:

```text
out/live-contracts/episode-creator/qwen3.6-flash/
20260723T095850Z-e7f01b96/
```

The three bounded attempts show:

- attempt 1: upstream request timed out;
- attempts 2 and 3: upstream connection errors;
- zero provider tokens for all three; and
- assistant trace messages with `stopReason: "error"`.

This is a persistent provider failure, so the paid sequence was stopped. Do not mark
the full gate passing merely because an earlier isolated Qwen run succeeded.

There is also a runtime provenance bug: `skillrace_next/runtime/pi.py` sets the Pi
result to `completed` whenever the container exits zero, even when the saved assistant
trace ends with `stopReason: "error"` and an `errorMessage`. Consequently, the failed
receipt incorrectly says completed with zero tokens and episode creation reports
"no assistant JSON" instead of a provider error.

## Uncommitted work that must be preserved

Current intended changes:

```text
M tests_next/live/test_episode_creator_live.py
M tests_next/live/test_tree_merge_live.py
```

`test_episode_creator_live.py` now:

- copies the genuine task prompt, NL checks, and skill text;
- adds both-model live segmentation of the immutable nine-call `deepClone` trace;
- requires target 9 and 7–9 validated episodes;
- requires concrete `deepClone`, primitive, array, and `TypeError` detail; and
- rejects generic episode purposes.

`test_tree_merge_live.py` was rewritten to build separate real-study trees from:

- two `deepClone` executions;
- one `findMissingNumber` execution;
- one `toKebabCase` execution; and
- two `csv-workbench` executions.

It asserts that:

- the two concrete `deepClone` implementation episodes merge;
- `deepClone`, `findMissingNumber`, and `toKebabCase` implementation nodes remain
  distinct;
- precise `sales.csv` creation can merge after adaptive segmentation separates it
  from analysis;
- all members and actual checker failures are preserved;
- cache replay uses no new Pi call; and
- every judgment has real Lab/model/token provenance.

The tree live test has passed syntax and collection checks but has not been run because
the prerequisite full episode live gate failed. Do not commit these live tests until
their required gates pass.

## Next actions, in order

1. Preserve the two live-test diffs and the unrelated manifest.
2. Use TDD in `tests_next/unit/test_pi_runtime.py` to reproduce a zero-exit Pi trace
   whose assistant message has `stopReason: "error"`.
3. Make `skillrace_next/runtime/pi.py` report that run as a provider/error result
   rather than `completed`. Keep the fix direct; do not add a recovery framework.
4. Run the focused Pi runtime tests and the complete offline suite. Commit only the
   runtime test and fix.
5. After the Lab/Qwen provider is stable, rerun the complete episode live gate:

   ```bash
   source ~/.bashrc
   PYTHONPATH=. pytest -q -s \
     tests_next/live/test_episode_creator_live.py --live
   ```

6. Manually inspect both fresh nine-call outputs for call-span grounding and semantic
   detail. Do not accept valid JSON alone.
7. Only after all six episode cases pass, run:

   ```bash
   source ~/.bashrc
   PYTHONPATH=. pytest -q -s \
     tests_next/live/test_tree_merge_live.py --live
   ```

8. Manually inspect the JavaScript and CSV trees. In particular, ensure feature
   implementation nodes did not merge under generic labels.
9. Scan fresh evidence for the active Lab key and confirm no owned container remains.
10. Commit only the two live-test files after both live gates pass.
11. Update `skillrace_next/docs/CURRENT_STATUS.md` and
    `skillrace_next/docs/FULL_STUDY_REMAINING_TODO.md` with the final evidence paths
    and semantic verdict; commit only those documentation changes.

Do not run the full scientific study, rename packages, or perform legacy cutover in
this continuation.

## Ready-to-paste prompt for the next agent

```text
Continue the SkillRACE Next adaptive episode/tree work from:

  skillrace_next/docs/2026-07-23-adaptive-episode-tree-session-handoff.md

Read that handoff, then read:

  skillrace_next/docs/2026-07-23-adaptive-episode-granularity-design.md
  skillrace_next/docs/2026-07-23-adaptive-episode-granularity-implementation-plan.md

Continue from Task 4; do not restart or redesign the pipeline.

Preserve the uncommitted changes in:

  tests_next/live/test_episode_creator_live.py
  tests_next/live/test_tree_merge_live.py

Do not touch the unrelated untracked:

  skillrace_next/study/base-images/manifest.json

First fix, with TDD, the Pi runtime bug that marks an assistant trace with
stopReason="error" as completed merely because Docker exited zero. Use the existing
tests_next/unit/test_pi_runtime.py and make the smallest direct fix in
skillrace_next/runtime/pi.py. Run focused and full offline tests and commit only that
runtime task.

Then, only when the Lab/Qwen provider is stable, rerun the complete paid episode live
gate with --live. Manually inspect the fresh DeepSeek and Qwen nine-call deepClone
segmentations for 7–9 concrete, span-grounded episodes. Do not treat an earlier
isolated Qwen success as replacing the required full gate.

Only after the complete episode gate passes, run the real-study tree live gate with
both lab/deepseek-v4-flash and lab/qwen3.6-flash. Manually verify that equivalent
deepClone implementation episodes merge, while deepClone, findMissingNumber, and
toKebabCase implementation nodes remain distinct and are not broadened into generic
workflow labels.

Stop on persistent provider failure. Do not mock, skip, or reinterpret a live gate.
Preserve sanitized evidence under out/live-contracts, scan for credentials, verify
Docker cleanup, commit the live tests only after their gates pass, and update
CURRENT_STATUS.md plus FULL_STUDY_REMAINING_TODO.md with the final evidence.

Work only in skillrace_next/ and tests_next/ (plus out/live-contracts evidence), never
import the old skillrace package, keep the implementation direct, and do not perform
the final package rename or legacy cutover.
```
