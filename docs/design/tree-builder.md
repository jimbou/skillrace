# Component 4 — Tree Builder (online merging)

> **Design spec** (Component 4, from the tex) — not yet implemented.

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
**temperature-0 caching** plus a **spurious-merge/split** self-correction. Merging is
**contextual** (only among a node's existing children), so "ran tests" at the start
and "ran tests" after a fix stay distinct nodes — the trajectory structure is
preserved.

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
  { "model": {"provider":"anthropic","id":"claude-opus-4-8","temperature":0},
    "merge_threshold": 0.5, "use_embedding_prefilter": true }
  ```

The Tree Builder reads **only** episodes (intent + summary{attempted,target,result}
+ canonical + opening_reasoning), never the raw trace, never the live container.

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
- **(ii) Self-correcting wrong merges (spurious-merge → split).** If two actions that
  actually behave differently were merged (e.g. attempt+target looked the same but
  the contexts differ enough to matter), a later test driven down that branch reveals
  the divergence; the loop classifies it as a **spurious merge** and the tree
  **splits** the node by the distinguishing feature (see the `split` section below).
  So a merge error degrades *search guidance temporarily*; it does **not** silently
  corrupt results. (This net is more important now that merging ignores outcome —
  over-merging on attempt+target is exactly what split repairs.)

**Determinism, honestly (tex §6).** The build is a deterministic *procedure* (same
seeds + same selection policy + temperature 0 + caching ⇒ same campaign), not an
order-invariant abstraction. Build stability is reported empirically: re-run a
campaign under a different selection seed and measure tree agreement.

### `split` — the self-correction the Tree Builder owns

`split(node, distinguishing_feature, forcing_run)` is a public Tree-Builder
operation invoked by the loop when a fold is classified **spurious merge**
([property-checker / loop classification](../build-plan.md)). It:

1. partitions `node.members` by `distinguishing_feature` (from the env/observation
   diff that the forcing run exposed),
2. creates two nodes, re-parents the out-edges accordingly,
3. invalidates the affected `MergeDecision` cache entries (so the corrected
   boundary sticks),
4. logs the `forcing_run` on the new branch.

Because split is a tree operation over episodes (not a re-run), it is testable
offline with fixed inputs.

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
- Plain code for the tree, the cache, the frontier, and `split`.

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

Per the global rule, this is the *same* model as every other judgment step; model
choice is the ablation axis, not a per-component decision.

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

### C. `split` (pure, no model)

`split_after_spurious_merge/` — a tree with a wrongly-merged node and a forcing run
that exposes a distinguishing feature; call `split`; assert the node partitions
correctly, edges re-parent, members are preserved (sum of split members == original
members), and the invalidated `pair_key`s are gone from the cache.

### D. Build stability (campaign-level)

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
| A wrong merge (two different situations merged) | Not fatal: surfaces later as a **spurious merge** when a test goes down that branch; the loop calls `split`. Degrades guidance temporarily; results stay sound (tex §6). |
| A wrong split / over-fragmentation | Extra branches = extra (validated, cheap-to-reject) frontier work, not wrong bugs. Reported via tree size vs. baseline. |
| `broaden` narrows a summary (drops coverage) | Caught by the monotonicity invariant test; broadening is rejected and retried; a node never disowns a member. |
| Model API error during a fold | The fold is **atomic**: on error the tree is left unchanged and the run is queued for re-fold; no partially-folded tree is committed. |
| Embedding prefilter wrongly discards a true match | Only an optimization; if `use_embedding_prefilter` rejects a pair the LLM would have merged, the run branches instead — later self-corrected by split. Prefilter can be disabled to test its effect. |

**Surfacing:** every merge verdict is written to the inspectable `MergeDecision`
cache with its rationale; the spurious-merge/split events are logged with the
forcing run. The tree's correctness is never asserted blindly — it is the
empirically reported build-stability and merge-calibration numbers.
