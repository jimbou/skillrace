# Legacy Episode and Tree Behavior Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the simplified SkillRACE episode splitter and global exact tree placement with the proven target-guided episode splitter and cached contextual semantic prefix merge.

**Architecture:** `episodes.py` owns deterministic trace projection, episode judgment, validation, and assembly. `reasoning_tree.py` owns the sole `behavior-tree/2` record, cached Pi judgments, and contextual folding; `branch_view.py` projects that tree for the existing selector/mutator, while `campaigns.py` persists the tree and judgment cache directly in SkillRACE state.

**Tech Stack:** Python 3.12, dataclasses already present in record types, JSON/JSONL, pathlib, hashlib, current Pi runner, pytest, Docker-backed live weak-agent evidence, DeepSeek v4 Flash, and Qwen 3.6 Flash.

---

## File map

- Create `skillrace_next/methods/episodes.py`: trace renderer, soft target, raw split validation, deterministic assembly, and Pi episode component.
- Create `skillrace_next/methods/episode_assets/example_input.txt`: owned worked trace used by the segmenter.
- Create `skillrace_next/methods/episode_assets/example_output.json`: owned correct six-episode split.
- Create `skillrace_next/methods/reasoning_tree.py`: tree schema, validation, cached model judgments, and contextual fold.
- Modify `skillrace_next/methods/skillrace.py`: remove the replaced episode/tree implementation and consume the new tree validator for proposals.
- Modify `skillrace_next/methods/branch_view.py`: index and isolate paths from `behavior-tree/2`.
- Modify `skillrace_next/pipeline/campaigns.py`: initialize/persist the new tree and `tree_merge_cache` and call the new modules.
- Create `tests_next/fixtures/traces/multi-call-and-narration.jsonl`: exercise multi-call turns, errors, and narration exclusion.
- Rewrite `tests_next/unit/test_episode_creator.py`: pure renderer/schema tests plus fake-Pi episode tests.
- Rewrite `tests_next/live/test_episode_creator_live.py`: separate real contracts for both study models.
- Rewrite `tests_next/unit/test_tree_merge.py`: tree invariants and semantic fold tests.
- Rewrite `tests_next/live/test_tree_merge_live.py`: separate real merge/cache contracts for both study models.
- Modify `tests_next/unit/test_edge_selector.py`: rich contextual-tree fixtures and guard assertions.
- Modify `tests_next/live/test_skillrace_proposal_live.py`: long contextual tree fixture for both models.
- Modify `tests_next/unit/test_campaign_commands.py`: state/cache integration assertions.
- Modify `tests_next/live/test_part1_tiny_live.py` and `tests_next/live/test_part2_tiny_live.py`: import/campaign expectations for the new records.
- Modify `skillrace_next/docs/PIPELINE.md`, `skillrace_next/docs/TESTING.md`, and `skillrace_next/docs/CURRENT_STATUS.md`: document the implemented records and completed gates.

No file under `skillrace/` is imported or modified. The generated untracked `skillrace_next/study/base-images/manifest.json` is not staged.

### Task 1: Deterministic trace projection and episode assembly

**Files:**
- Create: `skillrace_next/methods/episodes.py`
- Create: `tests_next/fixtures/traces/multi-call-and-narration.jsonl`
- Rewrite: `tests_next/unit/test_episode_creator.py`

- [ ] **Step 1: Write failing target and renderer tests**

Add tests with these exact public functions and assertions:

```python
from skillrace_next.methods.episodes import (
    assemble_episodes,
    project_trace,
    target_episode_count,
    validate_raw_episodes,
)


def test_target_episode_count_matches_legacy_table() -> None:
    assert [target_episode_count(n) for n in (0, 5, 6, 9, 10, 20, 45, 60, 100)] == [
        0, 2, 2, 3, 3, 6, 12, 14, 20
    ]


def test_projection_excludes_text_only_narration_and_numbers_tool_calls() -> None:
    rendered, calls = project_trace(MULTI_TRACE)
    assert [item["call"] for item in calls] == [1, 2, 3]
    assert "Final answer without a tool" not in rendered
    assert "result(ERROR)" in rendered
    assert calls[0]["reasoning"]
    assert calls[1]["reasoning"] == calls[0]["reasoning"]


def test_assembler_partitions_calls_and_attaches_verbatim_reasoning() -> None:
    _, calls = project_trace(MULTI_TRACE)
    raw = [
        {"start_call": 1, "end_call": 2, "purpose": "inspect inputs",
         "what_it_did": "read two files", "outcome": "one read failed"},
        {"start_call": 3, "end_call": 3, "purpose": "recover locally",
         "what_it_did": "used the available file", "outcome": "recovery succeeded"},
    ]
    assert validate_raw_episodes(raw, calls) == raw
    episodes = assemble_episodes(raw, calls)
    assert episodes[0]["opening_reasoning"] == calls[0]["reasoning"]
    assert episodes[1]["opening_reasoning"] == calls[2]["reasoning"]
    assert set(episodes[0]) == {
        "episode_id", "start_call", "end_call", "purpose",
        "what_it_did", "outcome", "opening_reasoning",
    }
```

Also test rejection of a gap, overlap, non-integer span, missing field, boundary on a
call without new reasoning, and an empty/tool-free trace.

- [ ] **Step 2: Run the new tests and confirm the expected import failure**

Run:

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_episode_creator.py
```

Expected: collection fails because `skillrace_next.methods.episodes` does not exist.

- [ ] **Step 3: Implement the minimum deterministic episode functions**

Create `episodes.py` with these constants and interfaces:

```python
RAW_EPISODE_FIELDS = {
    "start_call", "end_call", "purpose", "what_it_did", "outcome"
}
EPISODE_FIELDS = RAW_EPISODE_FIELDS | {"episode_id", "opening_reasoning"}
HEAD_LINES = 15
TAIL_LINES = 5


def target_episode_count(tool_call_count: int) -> int:
    if tool_call_count < 0:
        raise ValueError("tool call count must not be negative")
    if tool_call_count == 0:
        return 0
    return max(1, round(tool_call_count / (3.0 + tool_call_count / 50.0)))


def project_trace(trace_path: str | Path) -> tuple[str, list[dict[str, Any]]]:
    """Return the flat rendered trace and ordered source-grounded call records."""


def validate_raw_episodes(
    raw: Any, calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Require exact fields and an ordered partition of calls 1..N."""


def assemble_episodes(
    raw: list[dict[str, Any]], calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [
        {
            "episode_id": f"episode-{index}",
            **episode,
            "opening_reasoning": calls[episode["start_call"] - 1]["reasoning"],
        }
        for index, episode in enumerate(raw, 1)
    ]


def validate_episodes(
    episodes: Any, trace_path: str | Path
) -> list[dict[str, Any]]:
    rendered, calls = project_trace(trace_path)
    del rendered
    raw = [
        {name: episode[name] for name in RAW_EPISODE_FIELDS}
        for episode in episodes
    ]
    validate_raw_episodes(raw, calls)
    expected = assemble_episodes(raw, calls)
    if episodes != expected:
        raise ValueError("episode IDs or opening reasoning differ from the trace")
    return episodes
```

`project_trace` must parse tool results by `toolCallId`, include tool calls only from
assistant messages containing `toolCall`, place reasoning only on the first call in a
message while retaining it in the source record for every call, mark `isError`, truncate
long fields head/tail, and retain assistant/tool-result event IDs in each call record.

- [ ] **Step 4: Run focused deterministic tests**

Run:

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_episode_creator.py
```

Expected: all deterministic tests pass; fake-Pi tests remain absent until Task 2.

- [ ] **Step 5: Commit Task 1 only**

```bash
git add skillrace_next/methods/episodes.py \
  tests_next/fixtures/traces/multi-call-and-narration.jsonl \
  tests_next/unit/test_episode_creator.py
git commit -m "feat(skillrace-next): project grounded tool-call episodes"
```

### Task 2: Target-guided Pi episode creation and both-model live gate

**Files:**
- Create: `skillrace_next/methods/episode_assets/example_input.txt`
- Create: `skillrace_next/methods/episode_assets/example_output.json`
- Modify: `skillrace_next/methods/episodes.py`
- Modify: `skillrace_next/methods/skillrace.py`
- Modify: `skillrace_next/pipeline/campaigns.py`
- Modify: `tests_next/unit/test_episode_creator.py`
- Rewrite: `tests_next/live/test_episode_creator_live.py`

- [ ] **Step 1: Write failing fake-Pi orchestration tests**

Add a `raw_split()` fixture containing only the five raw fields. Test:

```python
def test_create_episodes_uses_target_example_temperature_zero_and_evidence(
    tmp_path: Path,
) -> None:
    episodes, receipt = create_episodes(
        run_record(tmp_path), config, tmp_path / "episodes", fake_pi
    )
    request = requests[0]
    assert request.provider == config.provider
    assert request.model == config.model_id
    assert request.temperature == 0
    assert request.allowed_tools == ()
    prompt = request.prompt_path.read_text(encoding="utf-8")
    assert "target episode count: 1" in prompt
    assert "WORKED EXAMPLE" in prompt
    assert "CONTINGENT" in prompt
    assert "ONLY from tool results" in prompt
    assert episodes == assemble_episodes(raw_split(), project_trace(TRACE)[1])
    creation = json.loads((tmp_path / "episodes" / "episode-creation.json").read_text())
    assert creation["tool_call_count"] == 2
    assert creation["target_episode_count"] == 1
    assert Path(creation["rendered_trace_path"]).is_file()
    assert receipt == request.output_dir / "receipt.json"
```

Add correction tests where attempt 1 is malformed JSON, attempt 2 has a partition gap,
and attempt 3 succeeds. Assert the fourth attempt is never made. Add a provider-status
test asserting `RuntimeError` without reinterpretation.

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_episode_creator.py
```

Expected: failures because `create_episodes` and the owned example assets are missing.

- [ ] **Step 3: Add the owned worked example and Pi loop**

Copy the reviewed legacy example text and six-episode JSON into the two owned asset files.
Implement this interface in `episodes.py`:

```python
def create_episodes(
    run: RunRecord,
    config: ExperimentConfig,
    output_dir: str | Path,
    pi_runner: PiRunner = run_pi,
) -> tuple[list[dict[str, Any]], Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rendered, calls = project_trace(run.trace_path)
    if not calls:
        raise ValueError("trace has no tool calls")
    rendered_path = output / "simplified-trace.txt"
    rendered_path.write_text(rendered, encoding="utf-8")
    target = target_episode_count(len(calls))
    diagnostic: str | None = None
    for ordinal in (1, 2, 3):
        attempt = output / f"episode-attempt-{ordinal}"
        attempt.mkdir()
        prompt_path = attempt / "prompt.txt"
        prompt_path.write_text(
            episode_prompt(rendered, target, diagnostic), encoding="utf-8"
        )
        result = pi_runner(
            PiRequest(
                operation_id=f"episodes.{run.run_id}.{uuid.uuid4().hex}",
                provider=config.provider,
                model=config.model_id,
                prompt_path=prompt_path,
                output_dir=attempt,
                image=config.docker_image,
                allowed_tools=(),
                max_turns=config.role_budgets["segmenter"],
                timeout_seconds=config.timeouts["provider"],
                temperature=0,
            )
        )
        if result.status != "completed":
            raise RuntimeError(f"Pi episode creation failed: {result.status}")
        try:
            response = assistant_json(result.trace_path)
            if not isinstance(response, dict) or set(response) != {"episodes"}:
                raise ValueError("episode response must contain only episodes")
            raw = validate_raw_episodes(response["episodes"], calls)
            episodes = assemble_episodes(raw, calls)
        except (json.JSONDecodeError, ValueError) as error:
            diagnostic = str(error)
            if ordinal < 3:
                continue
            raise ValueError("three invalid episode responses") from error
        atomic_write_json(output / "episodes.json", episodes)
        atomic_write_json(
            output / "episode-creation.json",
            {
                "schema": "skillrace-episode-creation/2",
                "run_id": run.run_id,
                "tool_call_count": len(calls),
                "target_episode_count": target,
                "rendered_trace_path": str(rendered_path),
                "episode_count": len(episodes),
                "pi_receipt_path": str(result.receipt_path),
            },
        )
        return episodes, result.receipt_path
    raise ValueError("three invalid episode responses")
```

Add `assistant_json(trace_path)` locally in `episodes.py`; it extracts the last assistant
text block and parses one raw JSON object, rejecting prose and Markdown fences. Add
`episode_prompt(rendered, target, diagnostic)` locally; it reads both owned example files,
states the decision-density and tool-result-only rules, requires one object whose sole
field is `episodes` containing the record array, and appends the concrete diagnostic only
on a correction attempt.

Remove the replaced trace/episode functions from `methods/skillrace.py`. Import
`episodes as episode_method` in `campaigns.py` and call
`episode_method.create_episodes`. Update all episode tests to import from `episodes.py`;
do not leave forwarding wrappers in `skillrace.py`.

- [ ] **Step 4: Run episode and campaign-import offline tests**

Run:

```bash
PYTHONPATH=. pytest -q \
  tests_next/unit/test_episode_creator.py \
  tests_next/unit/test_campaign_commands.py
```

Expected: all tests pass.

- [ ] **Step 5: Rewrite the separate live episode contract for both models**

Parameterize exactly:

```python
@pytest.mark.parametrize("model", ["deepseek-v4-flash", "qwen3.6-flash"])
@pytest.mark.parametrize("source_index", [0, 1])
def test_real_pi_segments_same_track_weak_agent_trace(
    model: str, source_index: int, live_evidence_root: Path
) -> None:
    source = latest_same_track_traces(model, count=2)[source_index]
    evidence = live_evidence_root / "episode-creator" / model / unique_run_id()
    trace_path = copy_trace_and_source_receipt(source, evidence)
    run = run_record_from_saved_trace(source, trace_path, model)
    config = live_config(evidence, model)
    episodes, receipt_path = create_episodes(run, config, evidence / "episodes")
    rendered, calls = project_trace(trace_path)
    del rendered
    assert 0 < len(episodes) <= 2 * target_episode_count(len(calls))
    assert validate_episodes(episodes, trace_path) == episodes
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["provider"] == "lab"
    assert receipt["model"] == model
    assert receipt["usage"]["total_tokens"] > 0
```

Implement `latest_same_track_traces` by scanning completed receipts under
`out/live-contracts/skillrace-ten-seed/<model>/` newest-first and requiring the matching
model plus an existing `execution/runtime/trace.jsonl`; return two distinct run paths
from the same skill campaign. Implement `unique_run_id` with
UTC timestamp plus eight UUID hex characters. Implement `copy_trace_and_source_receipt`,
`run_record_from_saved_trace`, and `live_config` directly in the live test using the
existing `RunRecord` and `ExperimentConfig` constructors; `live_config` sets
`provider="lab"`, `model_id=model`, role budget `{"segmenter": 8}`, and provider timeout
600 seconds. Copy the trace into
`out/live-contracts/episode-creator/<model>/<run-id>/input/trace.jsonl`, configure
the remaining fields exactly as the current live test does, and call only the episode
component. Also assert every opening reasoning equals its projected start call, every
outcome is nonempty, and no secret appears in evidence.

- [ ] **Step 6: Run and manually inspect each paid episode contract separately**

Run:

```bash
source ~/.bashrc
PYTHONPATH=. pytest -q -s tests_next/live/test_episode_creator_live.py \
  --live -k deepseek
PYTHONPATH=. pytest -q -s tests_next/live/test_episode_creator_live.py \
  --live -k qwen
```

Expected: each command runs two source traces and passes independently. Inspect both
`simplified-trace.txt` and `episodes.json` pairs; confirm each split groups one sub-goal
rather than each call, excludes final narration, and derives outcomes from displayed tool
results. Stop on persistent provider failure.

- [ ] **Step 7: Scan evidence and commit the completed episode component**

```bash
if rg -n -i '(authorization: bearer|api[_-]?key\s*[:=]|sk-[A-Za-z0-9_-]{12,})' \
  out/live-contracts/episode-creator; then exit 1; fi
git add skillrace_next/methods/episodes.py \
  skillrace_next/methods/episode_assets \
  skillrace_next/methods/skillrace.py \
  skillrace_next/pipeline/campaigns.py \
  tests_next/unit/test_episode_creator.py \
  tests_next/live/test_episode_creator_live.py
git commit -m "feat(skillrace-next): restore target-guided episode creation"
```

### Task 3: `behavior-tree/2` schema and deterministic prefix fold

**Files:**
- Create: `skillrace_next/methods/reasoning_tree.py`
- Rewrite: `tests_next/unit/test_tree_merge.py`

- [ ] **Step 1: Write failing tree schema and new-chain tests**

Define test helpers around this exact empty record:

```python
def empty_tree() -> dict[str, object]:
    return {
        "schema": "behavior-tree/2",
        "runs": {},
        "next_id": 0,
        "root_children": [],
        "root_edges": {},
        "nodes": {},
    }
```

Test that folding a two-episode first run creates `n0 -> n1`, records the run and both
full members, places `None + first.opening_reasoning` on the root transition, places
`first.outcome + second.opening_reasoning` on the internal transition, attaches a failure
to the requested episode's node, and makes no Pi call. Add validator failures for a cycle,
unreachable node, unknown child, duplicate membership, malformed transition, and invalid
variant/reach status.

- [ ] **Step 2: Run the tree tests and confirm import failure**

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_tree_merge.py
```

Expected: collection fails because `methods.reasoning_tree` is missing.

- [ ] **Step 3: Implement the tree records and deterministic fold path**

Create these interfaces:

```python
def empty_tree() -> dict[str, Any]:
    return {
        "schema": "behavior-tree/2", "runs": {}, "next_id": 0,
        "root_children": [], "root_edges": {}, "nodes": {},
    }


def validate_tree(tree: Any) -> dict[str, Any]:
    """Validate the complete rooted tree, membership, variants, and transitions."""


def merge_episodes(
    tree: dict[str, Any],
    episodes: list[dict[str, Any]],
    run_id: str,
    failures: list[dict[str, str]],
    merge_cache: dict[str, Any],
    config: ExperimentConfig,
    output_dir: str | Path,
    *,
    run_meta: dict[str, str] | None = None,
    pi_runner: PiRunner = run_pi,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fold one episode line and return validated tree plus updated cache."""
```

Use node fields `id`, `purpose`, `what_it_did_variants`, `runs`, `members`, `children`,
`edges`, `reach_status`, and `failure_ids`. Use member fields `run_id`, `episode_id`,
`purpose`, `what_it_did`, `outcome`, and `opening_reasoning`; variant fields `text` and
`run_ids`; transition fields `run_id`, `in_outcome`, and `reasoning`. The first run/new
suffix path is plain code and must not call Pi.

- [ ] **Step 4: Run deterministic tree tests**

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_tree_merge.py
```

Expected: deterministic schema/new-chain cases pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add skillrace_next/methods/reasoning_tree.py tests_next/unit/test_tree_merge.py
git commit -m "feat(skillrace-next): add contextual behavior tree records"
```

### Task 4: Cached semantic merge and both-model tree gate

**Files:**
- Modify: `skillrace_next/methods/reasoning_tree.py`
- Modify: `skillrace_next/methods/skillrace.py`
- Modify: `tests_next/unit/test_tree_merge.py`
- Rewrite: `tests_next/live/test_tree_merge_live.py`

- [ ] **Step 1: Write failing semantic merge tests**

Use a fake Pi responder keyed by operation ID. Fold:

```text
run A: inspect workspace -> run tests(PASS) -> report
run B: explore repository -> execute pytest(FAIL) -> repair -> execute pytest(PASS)
```

Return `{"same": true}` for inspect/explore and run-tests/pytest, `false` for
report/repair, generalized purposes for broaden calls, and same/different variant results.
Assert:

- the second run compares only root children and then children of its match;
- inspect/explore and run-tests/pytest share nodes despite different outcome;
- report and repair are different children of the shared test node;
- edge transitions retain PASS/FAIL and verbatim opening reasoning;
- merged purposes broaden and action variants retain run IDs;
- a second identical fold judgment uses cache without an extra Pi call;
- outcomes never appear in same-purpose prompts;
- every request uses provider/model from config and temperature `0`; and
- malformed responses receive at most two correction calls after the initial call.

- [ ] **Step 2: Run tests and confirm semantic assertions fail**

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_tree_merge.py
```

Expected: failures because contextual judgments, broadening, variants, and cache are not implemented.

- [ ] **Step 3: Implement exact legacy judgment flow through current Pi**

Implement a direct helper:

```python
def _cached_judgment(
    kind: str,
    payload: dict[str, Any],
    expected_fields: set[str],
    cache: dict[str, Any],
    config: ExperimentConfig,
    output: Path,
    pi_runner: PiRunner,
) -> dict[str, Any]:
    key = kind + ":" + canonical_json_hash(payload)
    if key in cache:
        return cache[key]
    diagnostic: str | None = None
    root = output / "judgments" / kind / key.split(":", 1)[1]
    root.mkdir(parents=True)
    for ordinal in (1, 2, 3):
        attempt = root / f"attempt-{ordinal}"
        attempt.mkdir()
        prompt_path = attempt / "prompt.txt"
        correction = (
            f"\nPrevious response invalid: {diagnostic}. Return corrected raw JSON."
            if diagnostic else ""
        )
        prompt_path.write_text(
            JUDGMENT_INSTRUCTIONS[kind]
            + correction
            + "\n\nINPUT:\n"
            + json.dumps(payload, sort_keys=True),
            encoding="utf-8",
        )
        result = pi_runner(
            PiRequest(
                operation_id=f"tree.{kind}.{uuid.uuid4().hex}",
                provider=config.provider,
                model=config.model_id,
                prompt_path=prompt_path,
                output_dir=attempt,
                image=config.docker_image,
                allowed_tools=(),
                max_turns=config.role_budgets["tree_alignment"],
                timeout_seconds=config.timeouts["provider"],
                temperature=0,
            )
        )
        if result.status != "completed":
            raise RuntimeError(f"Pi tree judgment failed: {result.status}")
        try:
            parsed = assistant_json(result.trace_path)
            if not isinstance(parsed, dict) or set(parsed) != expected_fields:
                raise ValueError(f"{kind} response fields are invalid")
        except (json.JSONDecodeError, ValueError) as error:
            diagnostic = str(error)
            if ordinal < 3:
                continue
            raise ValueError(f"three invalid {kind} responses") from error
        cache[key] = parsed
        atomic_write_json(root / "judgment.json", {
            "kind": kind,
            "cache_key": key,
            "result": parsed,
            "pi_receipt_path": str(result.receipt_path),
        })
        return parsed
    raise RuntimeError("tree judgment loop did not return")
```

Define `JUDGMENT_INSTRUCTIONS` with exact strict-JSON prompts for `same-purpose`,
`broaden-purpose`, and `same-approach`. Add the same local strict `assistant_json` parser
used by the episode module (small local duplication is intentional). Implement `_same_purpose`,
`_broaden_purpose`, and `_same_approach` as small prompt builders around this helper.
`_same_purpose` payload contains only purpose/actions, never outcomes. During fold, walk
from virtual root and inspect only current children; accept the first true result, broaden,
merge variants, add the transition, and descend. With no match, create a child and let the
empty-child suffix become new deterministically.

Write `tree.json`, `tree-merge-cache.json`, and `tree-merge.json` only after complete
validation. `tree-merge.json` records run ID, node/branch counts, judgment/cache-hit
counts, and evidence paths.

Remove the replaced tree constants, validator, alignment helper, and merger from
`methods/skillrace.py`; import only `validate_tree` there for proposal input validation.

- [ ] **Step 4: Run all episode and tree offline tests**

```bash
PYTHONPATH=. pytest -q \
  tests_next/unit/test_episode_creator.py \
  tests_next/unit/test_tree_merge.py
```

Expected: all tests pass.

- [ ] **Step 5: Rewrite the live merger as two independent model contracts**

Parameterize `deepseek-v4-flash` and `qwen3.6-flash`. For each model, load two real
episode lists produced by Task 2 for that same model. Fold the first list into an empty
tree, then fold the second through real Pi judgments. If the real traces do not contain a
semantically shared prefix and a later divergence, use two additional real weak-agent
traces for the same skill context and segment them first through the already-passed
episode component; do not fabricate episode summaries.

Assert at least one node has members from both runs, all members remain represented,
outcomes are excluded from same-purpose prompt files, a repeated judgment hits cache,
failure links remain attached, receipts name `lab` and the selected model with paid usage,
and evidence contains no secret.

- [ ] **Step 6: Run and manually inspect both paid merger contracts separately**

```bash
source ~/.bashrc
PYTHONPATH=. pytest -q -s tests_next/live/test_tree_merge_live.py --live -k deepseek
PYTHONPATH=. pytest -q -s tests_next/live/test_tree_merge_live.py --live -k qwen
```

Expected: each passes independently. Inspect the first merged node, member purposes,
variants, different outcomes, branch edges, cache entry, and Pi reasons for semantic
correctness. A syntactically valid but over-merged tree fails manual inspection.

- [ ] **Step 7: Scan and commit the completed tree component**

```bash
if rg -n -i '(authorization: bearer|api[_-]?key\s*[:=]|sk-[A-Za-z0-9_-]{12,})' \
  out/live-contracts/tree-merger; then exit 1; fi
git add skillrace_next/methods/reasoning_tree.py \
  skillrace_next/methods/skillrace.py \
  tests_next/unit/test_tree_merge.py \
  tests_next/live/test_tree_merge_live.py
git commit -m "feat(skillrace-next): restore cached contextual tree merging"
```

### Task 5: Rich edge index and branch isolation

**Files:**
- Modify: `skillrace_next/methods/branch_view.py`
- Modify: `skillrace_next/methods/skillrace.py`
- Rewrite: `tests_next/unit/test_edge_selector.py`
- Rewrite: `tests_next/live/test_skillrace_proposal_live.py`

- [ ] **Step 1: Write failing edge projection tests**

Build a 30-run `behavior-tree/2` fixture with a long observed chain and one branch. Assert
the compact card has exactly:

```python
{
    "edge_id", "source", "target", "reasoning",
    "previous_outcomes", "transitions", "failures"
}
```

Assert `previous_outcomes` is the stable unique list from transition `in_outcome`,
`reasoning` is the stable unique opening-reasoning summary, transition count is exact,
the root edge is excluded, and `isolate_branch` returns the unique root-to-edge nodes and
per-run transitions without including the merge cache.

- [ ] **Step 2: Run edge tests and confirm old-schema failure**

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_edge_selector.py
```

Expected: failure because `branch_view.py` expects flat `nodes`/`edges` lists.

- [ ] **Step 3: Rewrite branch projection directly for `behavior-tree/2`**

Keep the current SHA-256 edge ID over source/target. Iterate `root_children` and each
node's `children`, obtain transitions from `root_edges` or `node["edges"]`, and build the
new cards. Use one BFS/parent map from the virtual root for isolation; validate a unique
path and include source/target member outcomes and transition evidence.

Update the selector and mutator prompt field names in `skillrace.py`: describe
`previous_outcomes` as the observation before the selected reasoning edge. Do not pass the
full tree or judgment cache to either Pi cycle.

- [ ] **Step 4: Run focused selector tests**

```bash
PYTHONPATH=. pytest -q \
  tests_next/unit/test_edge_selector.py \
  tests_next/unit/test_tree_merge.py
```

Expected: all tests pass and the compact index remains smaller than the full long tree.

- [ ] **Step 5: Run both existing paid selector/mutator contracts on the new tree**

```bash
source ~/.bashrc
PYTHONPATH=. pytest -q -s tests_next/live/test_skillrace_proposal_live.py \
  --live -k deepseek
PYTHONPATH=. pytest -q -s tests_next/live/test_skillrace_proposal_live.py \
  --live -k qwen
```

Expected: each independently selects a real observed non-root edge, isolates its branch,
produces a feasible validated test, records both receipts, and exposes no credentials.
Manually inspect the first selected guard/outcome pairing for each model.

- [ ] **Step 6: Commit Task 5**

```bash
git add skillrace_next/methods/branch_view.py \
  skillrace_next/methods/skillrace.py \
  tests_next/unit/test_edge_selector.py \
  tests_next/live/test_skillrace_proposal_live.py
git commit -m "feat(skillrace-next): project contextual tree edges"
```

### Task 6: Campaign state integration

**Files:**
- Modify: `skillrace_next/pipeline/campaigns.py`
- Modify: `tests_next/unit/test_campaign_commands.py`
- Modify: `tests_next/live/test_part1_tiny_live.py`
- Modify: `tests_next/live/test_part2_tiny_live.py`

- [ ] **Step 1: Write failing state/cache integration tests**

Update fake episode/tree calls to the new modules and assert initial SkillRACE state is:

```python
{
    "schema": "skillrace-campaign-state/1",
    "phase": "initial_seeds",
    "execution_count": 0,
    "plan": plan,
    "tree": empty_tree(),
    "tree_merge_cache": {},
    "current_selection": None,
    "observations": [],
}
```

Assert `_updated_state` passes explicit `run_meta` with trace/evidence paths, accepts the
`(tree, cache)` result, persists both without dropping prior cache entries, links failed
checks to the last episode, and writes serializable state.

- [ ] **Step 2: Run campaign tests and confirm state mismatch**

```bash
PYTHONPATH=. pytest -q tests_next/unit/test_campaign_commands.py
```

Expected: failures on old root tree, missing cache, and old merge return signature.

- [ ] **Step 3: Implement direct state plumbing**

Import `episodes as episode_method` and `reasoning_tree as tree_method`. Delete the local
old `_root_tree`; initialize with `tree_method.empty_tree()` and `tree_merge_cache={}`.
Call:

```python
episodes, _ = episode_method.create_episodes(record, config, output / "episodes")
tree, merge_cache = tree_method.merge_episodes(
    state["tree"], episodes, record.run_id, failures,
    state["tree_merge_cache"], config, output / "tree",
    run_meta={
        "trace_path": str(record.trace_path),
        "artifact_path": str(record.artifact_path),
    },
)
```

Return both values in the next state. Do not add a state class, repository, migration, or
alternate old-schema path.

- [ ] **Step 4: Run all affected offline tests**

```bash
PYTHONPATH=. pytest -q \
  tests_next/unit/test_campaign_commands.py \
  tests_next/unit/test_episode_creator.py \
  tests_next/unit/test_tree_merge.py \
  tests_next/unit/test_edge_selector.py
```

Expected: all tests pass.

- [ ] **Step 5: Update and run tiny live campaign contracts**

Update only their tree/episode imports and assertions. Run each existing named contract
with `--live`; do not use the end-to-end run as a substitute for Tasks 2, 4, or 5.

```bash
source ~/.bashrc
PYTHONPATH=. pytest -q -s tests_next/live/test_part1_tiny_live.py --live
PYTHONPATH=. pytest -q -s tests_next/live/test_part2_tiny_live.py --live
```

Expected: the bounded campaigns persist `behavior-tree/2`, a nonempty episode list, and a
JSON judgment cache when a semantic comparison occurs. Provider failures stop the task.

- [ ] **Step 6: Commit Task 6**

```bash
git add skillrace_next/pipeline/campaigns.py \
  tests_next/unit/test_campaign_commands.py \
  tests_next/live/test_part1_tiny_live.py \
  tests_next/live/test_part2_tiny_live.py
git commit -m "feat(skillrace-next): persist contextual tree campaign state"
```

### Task 7: Full regression, evidence audit, and documentation

**Files:**
- Modify: `skillrace_next/docs/PIPELINE.md`
- Modify: `skillrace_next/docs/TESTING.md`
- Modify: `skillrace_next/docs/CURRENT_STATUS.md`

- [ ] **Step 1: Run the entire offline suite**

```bash
PYTHONPATH=. pytest -q tests_next/unit tests_next/integration
```

Expected: zero failures. Fix only regressions caused by the new episode/tree contracts,
using a new focused failing test before each production correction.

- [ ] **Step 2: Re-run every named component live gate independently**

```bash
source ~/.bashrc
PYTHONPATH=. pytest -q -s tests_next/live/test_episode_creator_live.py --live
PYTHONPATH=. pytest -q -s tests_next/live/test_tree_merge_live.py --live
PYTHONPATH=. pytest -q -s tests_next/live/test_skillrace_proposal_live.py --live
```

Expected: DeepSeek and Qwen cases pass in every file. No later campaign run substitutes
for an individual failure.

- [ ] **Step 3: Audit evidence and Docker cleanup**

```bash
for root in episode-creator tree-merger skillrace-edge-selector; do
  test -d "out/live-contracts/$root"
done
if rg -n -i '(authorization: bearer|api[_-]?key\s*[:=]|sk-[A-Za-z0-9_-]{12,})' \
  out/live-contracts/episode-creator \
  out/live-contracts/tree-merger \
  out/live-contracts/skillrace-edge-selector; then exit 1; fi
if docker ps -a --format '{{.Names}}' | rg '^skillrace-(run|check)-'; then exit 1; fi
```

Expected: evidence directories exist, credential scan finds nothing, and no owned
container remains.

- [ ] **Step 4: Document only the implemented behavior and evidence**

Update the three docs to state:

- episodes partition flattened tool calls around contingent decisions;
- the soft target formula and worked example are active;
- outcomes are model summaries constrained to tool results and opening reasoning is
  attached verbatim;
- the tree uses contextual child-only semantic purpose merging with outcomes excluded;
- caches, variants, members, and per-run transitions are authoritative state; and
- both study models passed their individual component contracts, naming evidence roots.

Remove current-status text describing global exact `purpose + outcome` placement. Do not
claim the full 30-test study has run.

- [ ] **Step 5: Verify docs/diff and commit Task 7**

```bash
git diff --check -- skillrace_next tests_next
git status --short -- skillrace_next tests_next
git add skillrace_next/docs/PIPELINE.md \
  skillrace_next/docs/TESTING.md \
  skillrace_next/docs/CURRENT_STATUS.md
git commit -m "docs(skillrace-next): document contextual episode tree pipeline"
```

- [ ] **Step 6: Record the final verified commit series without cutover**

```bash
git log --oneline -8
```

Expected: focused episode, tree, edge, campaign, and documentation commits are visible;
the generated image manifest and all unrelated dirty files remain unstaged. Do not rename
the package or remove the legacy package.
