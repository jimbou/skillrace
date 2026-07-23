# Legacy Episode and Tree Behavior Port

Date: 2026-07-23

Status: approved design; implementation not started

## Objective

Restore the proven episode segmentation and contextual semantic tree merge behavior from
the legacy implementation inside `skillrace_next`, while retaining the current provider,
Pi, evidence, campaign, checker, patching, and replay infrastructure.

This is a behavioral clean-room port. `skillrace_next` must not import `skillrace`, and
the implementation must remain direct functions, JSON records, loops, and conditionals.

## Evidence motivating the port

The current segmenter processes raw reasoning and tool-result events without an episode
count target or worked example. Saved live runs show over-segmentation, including final
reporting thoughts becoming episodes. The current merger searches globally for exact
`purpose + outcome` equality. Saved multi-seed trees contain no nodes with members from
more than one run.

The saved legacy four-run campaign gives the desired counterexample:

- runs with 9, 10, 6, and 5 tool calls became 3, 3, 2, and 2 episodes;
- four runs folded into six nodes;
- the initial diagnostic node contained all four runs; and
- one shared off-by-one repair node contained two runs.

The port therefore restores the mechanisms responsible for those results rather than
adding heuristics to the current simplified implementation.

## Scope

The port changes only:

- deterministic trace rendering for episode creation;
- the episode record and validation;
- the episode-generation prompt and worked example;
- behavior-tree representation, validation, and merge;
- the SkillRACE edge index and isolated branch view;
- SkillRACE campaign state needed to persist merge judgments; and
- focused unit and live contract tests for these components.

It does not change test generation, Random, VeriGrey, weak-agent execution, Codex
verification, patch admission, replay, study configuration, or package cutover.

## Episode creation

### Deterministic trace projection

Project the raw Pi JSONL trace into a flat sequence of actual tool calls numbered `1..N`.
Each projected call contains:

- the assistant reasoning attached to the first tool call in its assistant message;
- the tool name and compact arguments;
- the matching tool result and error flag; and
- the source assistant and tool-result event IDs.

Assistant messages without a tool call are excluded. Long argument or result bodies use
the legacy head/tail truncation rule. This projection is an internal prompt artifact; the
raw trace remains authoritative evidence.

### Soft episode target

For `N > 0`, compute:

```text
target = max(1, round(N / (3 + N/50)))
```

For `N == 0`, episode creation fails explicitly because current downstream campaign logic
requires at least one episode to associate check failures.

The target guides model granularity but is not a validity condition.

### Model judgment

Use the track's configured cheap model through the current Pi runner at temperature `0`.
The prompt includes:

- the flattened trace;
- the soft target;
- the legacy worked example, copied as owned `skillrace_next` test/runtime assets;
- the decision-density rule: group calls serving one sub-goal and split at contingent
  task-specific decisions, discoveries, and changes of approach; and
- the requirement that outcomes come only from tool results, never agent narration.

The model returns raw JSON containing ordered items with exactly:

```json
{
  "start_call": 1,
  "end_call": 3,
  "purpose": "reproduce and diagnose the failure",
  "what_it_did": "ran the tests and inspected the failing implementation",
  "outcome": "pytest reported two failing assertions"
}
```

### Deterministic assembly and validation

Validation requires:

- integer spans that partition `1..N` exactly, in order, with no gap or overlap;
- a boundary only at a tool call carrying assistant reasoning;
- nonempty `purpose`, `what_it_did`, and `outcome`; and
- source call references present in the projected trace.

The assembler assigns stable per-run `episode_id` values and attaches
`opening_reasoning` verbatim from the reasoning attached to `start_call`. The persisted
episode fields are exactly:

```json
{
  "episode_id": "episode-1",
  "start_call": 1,
  "end_call": 3,
  "purpose": "...",
  "what_it_did": "...",
  "outcome": "...",
  "opening_reasoning": "verbatim trace reasoning"
}
```

Malformed JSON or invalid spans receive the concrete diagnostic in a new Pi correction
attempt. At most three total attempts are allowed. Provider errors and exhausted invalid
responses remain failures; they are not accepted or mocked.

Each attempt, rendered trace, final episodes, receipt, target, and validation result is
saved under the component evidence directory.

## Behavior tree

### Tree record

Restore the contextual `behavior-tree/2` shape as the sole tree format used by the new
campaign. There is no compatibility reader or schema migration.

The tree contains:

- a run registry;
- `root_children` and per-run root transitions;
- nodes indexed by stable node ID; and
- child IDs and per-run transition records on each node.

Each node stores:

- its broadened `purpose`;
- distinct `what_it_did` variants and their run IDs;
- full episode members with run, episode, purpose, actions, outcome, and opening reasoning;
- children and transition records;
- reach status; and
- associated failure IDs.

Each transition stores the run ID, the previous episode's outcome (`null` at the root),
and the child episode's verbatim opening reasoning. Outcome is transition/guard evidence,
not node identity.

### Contextual prefix merge

Fold every incoming episode line from the virtual root:

1. Compare the next episode only with children of the currently matched node.
2. Ask whether each candidate has the same purpose, using `purpose` primarily and
   `what_it_did` as secondary context. Do not show or compare outcomes.
3. On the first same-purpose match:
   - append the full episode member;
   - broaden the node purpose to cover the previous purpose and new member;
   - merge the action into an existing same-approach variant or add a new variant;
   - add the per-run transition containing previous outcome and opening reasoning; and
   - descend into the matched child.
4. If no child matches, create a new child and continue. Its empty child set naturally
   makes the remaining suffix a new chain.

Nodes are never selected from another branch or by a global search. Different outcomes
for the same purpose remain members of the same node; any behavioral consequence appears
as differing next children and transition guards.

### Model calls and cache

Preserve the legacy judgment pattern:

- one same-purpose judgment for each contextual candidate until a match;
- one broaden-purpose judgment for a non-identical matched purpose; and
- same-approach judgments against stored variants until a match.

All use the track's cheap model through Pi at temperature `0`. Each response has strict
JSON validation and up to three total correction attempts. Pair judgments and broadened
purposes are cached by canonical content hash, including the judgment kind. A cache hit
makes no provider call.

The cache is a plain JSON object stored in `state["tree_merge_cache"]`, emitted in every
campaign state snapshot, and passed explicitly to the merger. Each new judgment also
saves its prompt, Pi receipt, parsed result, and cache key under the current merge evidence
directory.

## Edge selection integration

The existing two-cycle edge selector/mutator remains. Its compact index is rebuilt from
the contextual tree and exposes, for every real observed non-root edge:

- stable edge ID;
- source and target purposes;
- observed opening reasoning;
- observed previous outcomes;
- transition count; and
- failure count.

After selection, deterministic branch isolation follows `root_children` and node children
to produce the unique root-to-edge path. The isolated view contains node member outcomes
and per-run transition evidence needed by the mutator. It does not expose the merge cache.

## Failure and structural checks

Tree validation rejects:

- unknown child IDs;
- duplicate node IDs or duplicate run/episode membership;
- missing or malformed transition records;
- transitions whose run is absent from the relevant node memberships;
- cycles or nodes unreachable from the virtual root;
- empty purposes or invalid reach status; and
- malformed failure links or approach variants.

No partial tree is published after an invalid merge response or provider failure.

## Testing and live gates

Implementation proceeds in focused commits using red-green TDD:

1. deterministic renderer, target, assembler, and validation;
2. live episode creation through Pi;
3. tree schema and deterministic new-chain fold;
4. cached contextual same-purpose merge, broadening, variants, and transitions;
5. live multi-run tree merge through Pi;
6. edge index and branch isolation adaptation; and
7. campaign-state integration and regression suite.

Offline tests cover the legacy target table, narration exclusion, trace truncation,
reasoning-only boundaries, exact partitioning, tool-grounded example output, contextual
child matching, outcome-independent merging, no cross-branch jumps, cache reuse,
broadening, variants, transition evidence, failures, reach state, cycles, and branch
isolation.

Separate paid live contracts require `--live` and run with both `deepseek-v4-flash` and
`qwen3.6-flash`. They must demonstrate:

- semantically sensible episode counts and summaries on real weak-agent traces;
- tool-grounded outcomes and verbatim opening reasoning;
- two differently worded but same-purpose episodes merging;
- different-outcome same-purpose episodes sharing a node and then branching; and
- a cache hit avoiding a repeated provider judgment.

Sanitized evidence is saved beneath `out/live-contracts/episode-creator/` and
`out/live-contracts/tree-merger/`. The first output from each model is manually inspected;
valid JSON alone is not a passing gate. Persistent provider failure stops the task.

## Non-goals

This port does not add embeddings, a registry, a manager, a workflow engine, parallel
merge calls, schema migration, old-package imports, automatic split/recovery machinery,
or speculative frontier types. It does not perform the final package rename or legacy
cutover.
