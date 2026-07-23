<a href="../../README.md"><img src="../../skillrace-icon.png" alt="SkillRACE" width="54" align="right"></a>

# Component 4 — Tree Builder (online merging)

> **Component 4. v1 IMPLEMENTED in [`skillrace/tree.py`](../../skillrace/tree.py)** — the
> minimal job: fold one run's episode line into the global tree (purpose-merge, broaden,
> branch). **NOT in v1:** the frontier/selection and guard handoff (those sections below
> remain design-only, from the tex). (`split` from the tex was **dropped** — see the note
> under "Why similarity-merging is safe.") Reconciled with the current episode
> schema (`{intent, what_it_did, outcome, opening_reasoning}`): see
> [What v1 actually does](#whats-implemented-v1--skillracetreepy) and
> [The line-into-tree merge, step by step](#the-line-into-tree-merge-step-by-step-reconciled-with-the-episode-schema).

> tex §6 ("The behavior tree"). **Cheap model for merge decisions; plain code for
> the tree.** The merge decision is the single riskiest model step in the system —
> most of this page is about containing that risk and testing it in isolation.

---

## Purpose

Maintains the **behavior tree**: a growing structure whose **nodes are episodes**
(each "the agent attempted X on Y", carrying a summary) and whose **edges are the
prior outcome + the next episode's opening reasoning** that carried the run forward —
i.e. the edges *are* the branch conditions (guards). It folds each new run's episode
sequence into the tree, deciding where the run **shares** structure with prior runs
(merge) and where it **diverges** (branch), incrementally, and is used to expose the
next unexplored branch.

**Merging is on the episode's `attempt`+`target` (a similarity judgment), independent
of outcome** (Decision **D-TREE-1**): two episodes that attempted the same thing on
the same target are the **same node even if their outcomes differ** — and the
**differing outcome becomes the guard on the diverging *next* edge**, because a
different outcome is what led the agent to a different next decision. Merged node
summaries are **rewritten to generalize (broaden only)**; consistency rests on
**temperature-0 caching** (differing outcomes are handled by branching, not repair —
there is no `split`). Merging is
**contextual** (only among a node's existing children), so "ran tests" at the start
and "ran tests" after a fix stay distinct nodes — the trajectory structure is
preserved.

---

## What's implemented (v1 — `skillrace/tree.py`)

The **only** job of v1: given the global tree (a JSON file, created empty if missing) and
**one run's episode line**, **update the global tree**. No frontier and no guard synthesis
(and no `split` — that was dropped from the design). Model: `glm-4.5-flash` (the shared
judgment model), temperature 0, cached.

**Inputs.**
- `--episodes` — the segmenter's output (`episodes.json` / `episodes.raw.json`): a list
  with `start_call` / `intent` / `what_it_did` / `outcome`.
- `--session` — the run's **`raw/session.jsonl`** (the *agent-under-test* trace). Used
  ONLY to attach each episode's **`opening_reasoning`** = the reasoning of the assistant
  message owning its `start_call` (the reasoning that leads from the previous episode into
  this one). `start_call`/`end_call` are used **only** for this lookup, never for identity.
- `--tree` — the global tree JSON (and a sidecar `tree.cache.json` of judgment verdicts).

**Tree top level (`tree.json`).**
```json
{
  "schema": "behavior-tree/2",
  "skill": "mcp-server-patterns",
  "runs": { "<run_id>": {"dir": "runs/…", "session": "…/raw/session.jsonl", "episodes": "…/episodes.json"} },
  "root_children": ["n0"],
  "root_edges": { "n0": [ {"run","in_outcome": null, "reasoning"} ] },
  "next_id": 13,
  "nodes": { "n0": { … } }
}
```
- **`runs`** — the run registry: every folded run's **id → its directory** (+ the session
  and episodes files it came from), so any node/edge can be traced back to its run's
  artifacts.
- **`root_edges`** — the ROOT→level-0 entry edges, so **every transition is available as
  an edge** (not just parent→child): the entry into a level-0 node has `in_outcome: null`
  and the run's opening reasoning.

**A node (`nodes[id]`).**
```json
{
  "id": "n1",
  "intent": "<broadened purpose — the merge KEY>",
  "what_it_did_variants": [ {"text": "<a distinct approach>", "runs": ["runA", "runB"]} ],
  "runs": ["runA", "runB"],
  "members": [ {"run","episode_index","intent","what_it_did","outcome","opening_reasoning"} ],
  "children": ["n2","n7"],
  "edges": { "n2": [ {"run","in_outcome","reasoning"} ], "n7": [ … ] }
}
```
- **`runs`** — which run(s) this node belongs to. A node merged from several runs lists
  them all (e.g. the `n0`/`n1` prefix shared by two runs).
- **`members`** — one record per merged episode (so per-run provenance is never lost).
- **`edges`** — out-edges keyed by child id; **each edge record names its `run`**, plus
  the guard data (`in_outcome` = the parent's outcome for that run, `reasoning` = the
  child's opening reasoning). Every run's transition is its own record, so a branch node
  carries one edge record per run that took each fork.

**Three model judgments, all cached (`tree.cache.json`):**
1. **`same_purpose(ep, node)`** — the merge decision. Decided on **purpose** (`intent`),
   with the node's variants as secondary context; **merges even when done a slightly
   different way**; **ignores outcome**. This is the contextual child-match in the fold.
2. **`broaden_intent(cur, new)`** — on every merge, the node's `intent` (the key) is
   **regeneralized** to cover the new member's purpose too (broaden only, never narrow),
   so the merge key stays representative as members accumulate.
3. **`same_approach(a_did, b_did)`** — on every merge, the member's `what_it_did` is
   matched against the node's existing `what_it_did_variants`: **same way → record the run
   on that variant; genuinely different way → add a new variant.** So a node keeps the
   *distinct* approaches its members took, deduped.

**Edges carry the guard data** (recorded but not yet *consumed* in v1): each parent→child
edge stores, per run, the parent's **`in_outcome`** and the child's **`opening_reasoning`**
— exactly the two guard signals Component 5 will later read.

---

## The line-into-tree merge, step by step (reconciled with the episode schema)

> The concrete walkthrough to validate the merge process, reconciled with the **current
> episode schema** (`{intent, what_it_did, outcome, opening_reasoning}` from the
> segmenter) and the "level-by-level" mental model. The pseudocode under
> [The fold algorithm](#the-fold-algorithm-how-a-run-merges-in) is the precise form;
> this section is the intuition + a worked example.

**Field reconciliation.** The tex's `attempt`+`target` is carried by our **`intent` +
`what_it_did`** (what the episode tried, and on what); the tex's `result` is our
**`outcome`**:

- **Merge key (node identity):** the **`intent`** (purpose), compared by `same_purpose`
  (a similarity judgment that merges even when the two went about it a slightly different
  way) — **`outcome` is excluded**. On merge the `intent` is **broadened** to stay
  representative; `what_it_did` is kept as **distinct variants** (same way collapses,
  different way is added) — see [v1](#whats-implemented-v1--skillracetreepy).
- **Edge / guard:** the parent episode's **`outcome`** + this episode's
  **`opening_reasoning`** (the reasoning before its first tool call — the thing that
  "points to" the node).

**Inputs.** The incoming run is a **LINE** `L = [e0, e1, …, ek]` (episodes in order; not
a tree). The global tree `T` is rooted at a virtual **ROOT** whose children are the
**level-0 nodes** (every run's first episode). "Level x" = depth x from the root.

**First run.** If `T` is empty, `T := L` exactly: `e0→e1→…→ek` is a linear chain under
the root. (The first test's episode line is the initial tree.)

**Folding a line into an existing `T` (prefix-merge):**

```text
node = ROOT                                  # node.children = the level-0 episodes
for x, e in enumerate(L):                    # walk the line top→down, level by level
    cand = node.children                     # ← candidate set at level x  (see NOTE)
    match = first c in cand with same_purpose(e, c)  # cached judgment on PURPOSE (intent)
    if match exists:
        add e as a member of match           # same purpose ⇒ same node
        broaden_intent(match)                # regeneralize the merge key
        merge_variant(match, e)              # same way → collapse; different way → add variant
        edge(node→match): in_outcome = prev.outcome, reasoning = e.opening_reasoning
        node = match                         # DESCEND, continue to level x+1
    else:
        new = fresh_node(e); link node→new (same edge signal)
        graft the REST of L (e_{x+1..k}) as a fresh chain under new
        BREAK                                # DIVERGE — stop comparing further levels
    prev = e
```

**NOTE — the candidate set is the matched node's CHILDREN, not "all nodes at level x".**
At the root these coincide (root's children = all level-0 nodes), which is why "compare
the head against every level-0 node" is exactly right. Deeper down, "all nodes at level
x" would include nodes under *other* branches; merging into one of those makes the line
**jump branches** and gives a node members whose root-to-node action prefix doesn't
match. So once we've matched a node at level x−1 we only consider **that node's
children** at level x. (They're a subset of level-x nodes; they equal "all level-x
nodes" only while the tree is still a single line — the common early case.)

**Why "stop after the first mismatch."** A line is a single path. The instant its action
at level x matches nothing in the candidate set, it has left the explored region; the
remaining suffix is all-new by construction (it can't match a subtree that doesn't
exist), so it grafts on as a fresh branch and comparison stops. The loop gives this for
free: after a fresh node, its `children` set is empty, so every later episode also
fails to match → fresh chain.

**Where the branch (and the guard) appear.** A node becomes a **branch** the moment it
has >1 out-edge — two runs shared the prefix up to it, then diverged. The divergence is
explained by the parent's differing **`outcome`** (and/or differing `opening_reasoning`)
— that is the guard the synthesizer later negates ([guards](./guard-synthesizer.md)).

**Worked example (3 runs).**

```text
Run A:  scaffold → install-deps → run-tests(PASS) → done
   T empty  ⇒  T := the A line.

Run B:  scaffold → install-deps → run-tests(FAIL) → fix → run-tests(PASS) → done
   L0 scaffold      vs {scaffold}      → same  ⇒ merge, descend
   L1 install-deps  vs {install-deps}  → same  ⇒ merge, descend
   L2 run-tests     vs {run-tests}     → same  ⇒ MERGE — same node even though
                                                  A passed and B failed (outcome NOT in key);
                                                  node now has 2 members, outcomes {PASS, FAIL}
   L3 fix           vs {done}          → different ⇒ DIVERGE: "fix→run-tests→done" grafts on
   ⇒ run-tests is now a BRANCH: edge→done carries in_outcome=PASS;
                                 edge→fix  carries in_outcome=FAIL.   ← the outcome became the guard

Run C:  scaffold → build-docs → …
   L0 scaffold   vs {scaffold}            → same      ⇒ merge, descend
   L1 build-docs vs {install-deps}        → different ⇒ DIVERGE at level 1
   ⇒ scaffold becomes a branch; C's suffix grafts on.
```

This yields exactly the shared-prefix / branch-on-divergence structure we want, and the
branch points are where outcomes/decisions differ — the fuel for guard synthesis.

---

## Input contract

- The current [`BehaviorTree`](../data-contracts.md#6-behaviortree--state-of-component-4)
  (or empty, for the first run).
- One run's [`Episode[]`](../data-contracts.md#5-episode--the-trees-atom-segmentation--summary)
  sequence (the join output of Components 2+3).
- The [`MergeDecision`](../data-contracts.md#7-mergedecision--the-cached-unit-of-component-4s-model-step)
  cache (read/write).
- Config:
  ```json
  { "model": {"provider":"yunwu","id":"glm-4.5-flash","temperature":0},
    "merge_threshold": 0.5, "use_embedding_prefilter": true }
  ```

The Tree Builder reads **only** episodes (`intent` + `what_it_did` + `outcome` +
`opening_reasoning` — `intent`+`what_it_did` are the merge key; `outcome` is excluded
from it and used for the edge/guard), never the raw trace, never the live container.

---

## Output contract

- The updated [`BehaviorTree`](../data-contracts.md#6-behaviortree--state-of-component-4)
  (nodes, edges, branches).
- The derived [`Frontier`](../data-contracts.md#61-frontier) (worklist of untried
  branch alternatives, with priorities).
- New [`MergeDecision`](../data-contracts.md#7-mergedecision--the-cached-unit-of-component-4s-model-step)
  records appended to the cache (the auditable trail of every same/different
  verdict).

The tree is also serializable to a stable canonical form (sorted keys, member lists
sorted by `(run_id, episode_index)`) so two builds can be diffed for the **build
stability** number (tex §6).

---

## The fold algorithm (how a run merges in)

```
node = root
prev_outcome = None
for i, ep in enumerate(run.episodes):          # walk the new run from the root
    # MERGE on attempt+target, contextually (only among this node's children):
    child = None
    for c in node.children:
        if same_action(ep, c):                 # ← the model judgment, cached: same attempt+target?
            child = c; break
    if child is not None:                       # MERGE the episode into a known node
        child.members.append((run_id, i))
        broaden(child.summary, ep)              # rewrite to GENERALIZE (broaden only)
    else:                                       # NEW node (a new action at this point)
        child = fresh_node(ep)                  # node summary := ep summary
        link(node -> child)

    # the EDGE carries the transition signal = (outcome of prev episode, reasoning of THIS one):
    edge = get_or_create_edge(node, child)
    edge.members.append({run_id, episode_index: i,
                         in_outcome: prev_outcome,        # what the parent node produced for this run
                         reasoning: ep.opening_reasoning}) # why the agent took THIS edge

    if node.out_edges_count > 1:                # node now has >1 distinct transition → BRANCH
        mark_branch(node)                       # a guard lives here (Component 5); outcome discriminates

    node = child
    prev_outcome = ep.result                    # episode's outcome (read from tool output, Comp. 3)
update_frontier()
```

### The rules, precisely (D-TREE-1)

1. **Same action ⇒ merge node + broaden.** `same_action(a, b)` compares **only the
   episodes' `attempt`+`target`** (not the outcome). A match merges the episode into
   the existing child node; that node's summary is **rewritten to generalize over all
   its members** — *broadening only*. Invariant: after broadening, the summary still
   covers every member (a node never disowns a run it already represents). Broadening
   is a separate (cheap) model call, or a deterministic union for the structured
   fields; it must be **monotone** (members only added, coverage only widened).
2. **Different action ⇒ new node.** A genuinely different next attempt+target makes a
   new child node and a new out-edge.
3. **>1 out-edge ⇒ branch; the outcome is the guard.** When a node has more than one
   distinct out-edge, it is a **branch**. The runs took different edges *because*
   their outcomes (and/or reasoning) at that node differed — so the **edge-carried
   `in_outcome` is the primary guard discriminator**, with `reasoning` as the second
   signal ([guard-synthesizer.md](./guard-synthesizer.md)). Same outcome but different
   next edge ⇒ the guard is carried by `reasoning` alone (the agent chose differently
   in the same state — itself interesting).
4. **Similarity, not equality.** `same_action` is a similarity judgment (cheap model,
   optionally embedding-prefiltered) over attempt+target, not string comparison: it
   merges "ran the suite" with "ran pytest" but keeps "ran the suite" vs "ran the
   linter" apart. Outcome text is **excluded** from the comparison by construction.

---

## Why similarity-merging is safe here (and how the risk is contained)

The risk is that a similarity judgment is not perfectly consistent. Contained two
ways, both of which are explicit, testable mechanisms — **document and test both**:

- **(i) Temperature-0 + caching.** Every `same_action` verdict is made at temperature
  0 and **cached by a content hash of the two episodes' attempt+target** (`pair_key`
  in [MergeDecision](../data-contracts.md#7-mergedecision--the-cached-unit-of-component-4s-model-step)).
  The same action pair therefore **always** gets the same verdict within a campaign.
  The cache *is* the consistency guarantee; it is an inspectable artifact.
- **(ii) Differing outcomes are handled by the structure, not by repair.** Merging two
  same-purpose episodes that turned out differently is **correct**, not a problem: the
  differing `outcome` is recorded on the node's members and on the out-edges, and the
  runs' **next** episodes — which differ *because* the outcomes differed — become
  distinct children, i.e. a **branch**, with the outcome as its guard. Wherever
  behaviour truly diverges, it diverges at the first episode whose **purpose** differs,
  so a new child appears there; merging the earlier same-purpose episodes never hides
  it. (Worked example: the "install deps" node where one run succeeds and one fails on a
  missing compiler — they share the node, their outcomes differ, and their next episodes
  branch.) The only genuinely *wrong* merge is a `same_purpose` **misjudgment** (the
  model calls two unrelated purposes "same"); the defence against that is **conservative,
  contextual merging** (already done) plus the fact that `members` keeps full
  provenance, so an over-merged node is always inspectable. There is **no automatic
  `split`** — it was dropped (see note).

**Determinism, honestly.** The build is a deterministic *procedure* (same seeds + same
selection policy + temperature 0 + caching ⇒ same campaign), not an order-invariant
abstraction. Build stability is reported empirically: re-run a campaign under a
different selection seed and measure tree agreement.

> **Note — `split` was removed from the design.** The tex proposed a "spurious-merge →
> split" self-correction. On review it solved a non-problem: same-purpose / different-
> outcome merges are correct and self-resolve via the recorded outcome + downstream
> branching (above). The only residual case is a `same_purpose` false-positive, which is
> better avoided by conservative merging than repaired by a split machine — so split is
> not part of this component.

---

## Branch frontier & selection priority

The Tree Builder emits the [`Frontier`](../data-contracts.md#61-frontier): the
untried out-edges (and "propose a new sibling" slots) at each branch. Each item gets
a `priority` so the loop knows what to explore next. The score blends three signals
(Decision **D-TREE-2**, from how interesting a divergence is likely to be):

- **fan-out** — a node with **more out-edges** has shown the skill branches a lot
  here, so it is interesting; weight up by branching factor.
- **mid-depth preference** — prefer branches that are **neither trivially shallow nor
  right at the end** of the trajectory, so the synthesized change produces a
  meaningful, non-cosmetic divergence (a bell-curve over depth, peaked in the middle
  of typical run length).
- **novelty / exploration history** — **never-explored** branches first; down-weight
  branches we have already pushed on (and hard-skip ones already exhausted).

```
priority(branch) = w_f · norm(fanout) + w_d · middepth(depth) + w_n · novelty(branch)
```

Weights are config; the **selection is seeded** so a campaign is reproducible
(tex §6 build stability). The loop's policy still also boosts branches on paths
toward property-relevant regions (e.g. before a commit) — that boost is applied by
the loop, not baked into the Tree Builder, so the Builder stays property-agnostic.

**Mutation handoff (diverse siblings).** Because a branch node can already have
several siblings, the Tree Builder hands the synthesizer **all observed out-edge
conditions at that node**, and the synthesizer is asked for a **new, diverse** one
(distinct from every existing sibling) rather than merely negating one
([guard-synthesizer.md §5b](./guard-synthesizer.md#5b-candidate-synthesizer-negate--mutate)).

---

## Dependencies

**Needs:**
- The **judgment model** via a **direct provider API call** (not Pi) for
  `same_action` and for broadening — temperature 0, cached.
- (Optional) an **embedding model** to prefilter obvious non-matches cheaply before
  the LLM call; a code-level optimization, not a correctness dependency.
- Plain code for the tree, the cache, and the frontier.

**Does NOT depend on:**
- Pi, Docker, the container, the raw trace, the property checker.
- The synthesizer or runner — it *produces* the frontier those consume but never
  calls them.

---

## The model's role

- **Makes a model call:** yes — `same_action(a,b)` per candidate child (cache miss
  only), and `broaden(summary, ep)` on each merge.
- **What it decides:** *only* "did these two episodes attempt the same thing on the
  same target?" (`same`/`different` + confidence + rationale) and "rewrite this
  summary to also cover this new member without narrowing it." It does **not** decide
  tree topology — the code does, mechanically, from the verdicts.
- **Prompt (`same_action`):** present episode A and B as `attempt / target` (plus
  `canonical` for context); ask "Did these attempt the **same kind of action on the
  same target**? **Ignore how they turned out (the outcome) and which tool calls were
  used.** Answer JSON `{verdict:'same'|'different', confidence, rationale}`." Excluding
  the outcome is what makes the *outcome* available as the edge/guard, and excluding
  tactics is what lets two runs that reached the same point by different tool calls
  merge (tex §7).
- **Output format:** strict JSON → a `MergeDecision`. `verdict` is the only
  load-bearing field; `confidence`/`rationale` are for audit.

Per the global rule, this is the *same frozen model* as every other judgment step;
there is no per-component model choice or headline model sweep.

---

## How to test it in isolation

(The merge decision especially.)

### A. The merge **decision**, against a labeled pair set (the critical test)

Build a **fixed dataset of episode pairs with ground-truth `same`/`different`
labels**, where the label reflects **attempt+target only, ignoring outcome**
(`tests/fixtures/tree/merge_pairs.jsonl`), e.g.:

```jsonl
{"a":{"attempt":"ran the test suite","target":"pytest suite","result":"ImportError in auth"}, "b":{"attempt":"ran the suite","target":"pytest","result":"1 assertion failed in test_login"}, "label":"same"}
{"a":{"attempt":"ran the test suite","target":"pytest suite","result":"ImportError"}, "b":{"attempt":"ran the linter","target":"ruff","result":"all checks passed"}, "label":"different"}
{"a":{"attempt":"edited the implementation","target":"auth.py","result":"applied 1 hunk"}, "b":{"attempt":"edited the implementation","target":"views.py","result":"applied 2 hunks"}, "label":"same"}
```

The first pair is **the defining case (D-TREE-1):** same action (ran the suite),
**different outcomes** (import vs assertion) → labeled `same` (they merge into one
node); the outcome difference is what later branches the *next* edge.

Two test modes:
- **Offline determinism/consistency** (no live model): a recorded responder; assert
  (1) `same_action` is a pure function of `pair_key` (which hashes attempt+target,
  **not** outcome — so the first pair hits the same key regardless of its differing
  results), (2) it is **symmetric** (`(a,b)` and `(b,a)` agree — enforced by
  canonicalizing the pair before hashing), (3) cache hits never call the model.
- **Calibration against labels** (live model, run in CI nightly or on demand):
  report agreement with the human labels (precision/recall of `same`). This is the
  component's calibration number; a regression here is the early warning that the
  riskiest step has drifted.

### B. The **fold** mechanics (pure, no model)

With `same_action` stubbed to a lookup table, assert tree topology:
- `fold_two_identical_runs/` — two runs with all-`same` actions **and same outcomes**
  ⇒ a single linear path, every node has 2 members, **no branch**.
- `fold_same_action_diff_outcome/` — **the D-TREE-1 case:** two runs whose first
  episode is the same action but different outcome, leading to different second
  actions ⇒ the first episode is **one merged node** (2 members), which is a
  **branch** with two out-edges, and **each out-edge carries the corresponding
  `in_outcome`** (assert edge A's outcome = ImportError, edge B's = AssertionError).
  This is the test that the outcome became the guard, not part of the node.
- `fold_divergent_actions/` — two runs whose next *action* differs ⇒ a branch with
  two child nodes.
- `broaden_is_monotone/` — after a merge, assert the node's member set only grew and
  the summary still "covers" both members; assert no member was dropped.
- `frontier_priority/` — assert branch priority follows the policy (below):
  higher fan-out + mid-depth + never-explored rank first.

### C. Build stability (campaign-level)

`stability/` — fold the same set of runs in two different orders / seeds; assert
tree agreement ≥ threshold and report the number (tex's empirical determinism).

### Test shape

```python
def test_same_action_ignores_outcome_and_is_cached():
    # same attempt+target, DIFFERENT outcomes → still "same" (merge), one cached call
    a = ep(attempt="ran the suite", target="pytest", result="ImportError")
    b = ep(attempt="ran the suite", target="pytest", result="1 assertion failed")
    v1 = same_action(a, b, model=spy)         # cache miss → 1 model call
    v2 = same_action(b, a, model=spy)         # symmetric + cached → 0 calls
    assert v1.verdict == v2.verdict == "same"
    assert spy.calls == 1                      # pair_key hashes attempt+target only
```

---

## Failure modes

| Situation | Behavior |
|-----------|----------|
| Model gives inconsistent verdicts across calls | Impossible to observe within a campaign: the **cache** pins the first verdict for each `pair_key`. Cross-campaign drift is measured by the calibration test, not silently absorbed. |
| Same purpose, different outcome, merged into one node | **Correct, not a fault:** outcomes are recorded on members/edges and the runs' next episodes branch — the difference surfaces downstream, with the outcome as the guard. |
| A `same_purpose` misjudgment (two unrelated purposes merged) | Avoided by conservative, contextual merging; `members` keeps full provenance so an over-merged node stays inspectable. No automatic repair (split was dropped). |
| `broaden` narrows a summary (drops coverage) | Caught by the monotonicity invariant test; broadening is rejected and retried; a node never disowns a member. |
| Model API error during a fold | The fold is **atomic**: on error the tree is left unchanged and the run is queued for re-fold; no partially-folded tree is committed. |
| Embedding prefilter wrongly discards a true match | Only an optimization; if `use_embedding_prefilter` rejects a pair the LLM would have merged, the run branches instead. Prefilter can be disabled to test its effect. |

**Surfacing:** every merge verdict is written to the inspectable verdict cache with its
rationale. The tree's correctness is never asserted blindly — it is the empirically
reported build-stability and merge-calibration numbers.
