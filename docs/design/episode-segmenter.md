# Component 2 — Episode Segmenter

> **REVISED design (supersedes the original tex-derived spec).** Status: the
> deterministic front half — the **simplified-trace renderer + episode-count target**
> — is implemented in [`skillrace/simplify_trace.py`](../../skillrace/simplify_trace.py).
> The **segmenter agent** and the **deterministic assembler** are designed here and
> not yet implemented.

> This component splits one run's trace into **episodes** and (in the same step)
> summarizes each — see [episode-summarizer.md](./episode-summarizer.md) for the
> summary half, which is now **fused into the same agent call**.

---

## What changed from the original spec, and why

The first spec (windowed, causal, one **direct temp-0 model call per window**, over a
**normalized frozen `Trace`**) was reworked after building the rest of the pipeline:

| Original | Now | Why |
|---|---|---|
| Normalize the trace into a frozen `Trace` contract first | **No normalization.** Render `session.jsonl` straight into a readable `simplified_trace.txt`; episodes reference the original tool-call indices | We never string-match across runs (the tree merges on model judgment), so a normalized contract was dead weight. |
| One **direct provider call** per window | A **pi agent** reads the simplified-trace file | Long traces don't fit one call (a real run hit **2.8M input tokens**). An agent can **page** the file and commit episodes incrementally. |
| **Windowing + causal uncommitted-tail carry** in code | **Dropped for v1** | Our traces fit comfortably; the agent itself handles long traces by reading head-first. Revisit only if needed. |
| Segment now, **summarize in a separate component** | **Fused**: the agent emits boundaries **and** summaries together | The agent has already read each episode; a second per-episode pass is wasted cost. |
| Boundaries = generic "goal switches" (orient→read→build→verify) | Boundaries = **contingent decisions** (decision-density, not uniform phases) | Generic phases are identical across every build task, so negating their guards yields identical tests. Fine-grained *decisions* are what make synthesized tests differ. |

The one rule that did **not** change: an episode's **outcome is read from tool
results, never from the agent's narration** (see the summarizer doc).

---

## Pipeline (artifacts, not function calls)

```text
runs/<run>/raw/session.jsonl                      (raw pi trace)
        │
        ▼  skillrace/simplify_trace.py   (DETERMINISTIC — no model)
runs/<run>/simplified_trace.txt   +   target_episodes ≈ round(N / (3 + N/50))
        │
        ▼  segmenter AGENT (pi, cheap model, reads the .txt; few-shot examples)
runs/<run>/episodes.raw.json      (episode spans + per-episode summary)
        │
        ▼  assembler   (DETERMINISTIC — validate + attach edges)
runs/<run>/episodes.json          (per-run episode CHAIN: nodes=summaries, edges=opening reasoning)
        │
        ▼  Tree Builder (Component 4) — folds the chain into the GLOBAL behavior tree
```

---

## Step 1 — Simplified-trace renderer (deterministic, implemented)

`simplify_trace.py` folds the raw `session.jsonl` (assistant `thinking`/`toolCall`
blocks + separate `toolResult` lines) into one readable file. Rules:

- **A FLAT sequence of tool calls** numbered GLOBALLY `1..N` — *not* chunked into
  turn/episode-looking blocks, so the rendered trace is **unsegmented** and does not
  telegraph where episodes are. These indices are what the agent uses to mark spans.
- **Reasoning is shown INLINE** (`reasoning:` line) on the **first tool call of each
  assistant message** — i.e. wherever a new `thinking` block begins. A `reasoning:`
  line marks a *candidate* boundary; the agent decides which candidates actually start
  an episode and **groups** the rest (an episode may merge several reasoning blocks).
- **A boundary may only fall at a `reasoning:` line** (a new thinking block). This is
  what makes the edge between two episodes a *real* piece of reasoning (see Edges).
- **Truncation**: any long field is cut to the first **15** + last **5** lines with a
  `… (k lines truncated for brevity) …` marker. Applies to `bash`/`read` **results**
  and `write`/`edit` **args bodies**.
- **No duplication**: the "big" content sits in exactly one place per tool —
  `write`/`edit` content is in **args** (the result is a short `Successfully wrote N
  bytes` status, kept as the grounded outcome); `read`/`bash` content is in the
  **result** (args is just the path/command). So nothing is shown twice.
- **Errors are marked**: a failing tool result is rendered as `result(ERROR):` (read
  from pi's `isError` flag) — e.g. a build that actually failed.

The header records `run_id`, `skill`, the `prompt`, `tool_calls=N`, and the target.

## Step 2 — Episode-count target (deterministic, implemented)

A **smooth, monotonic, saturating** target replaces the original tiered divisor
(which gave the same target for 45 and 60 calls and even dropped across tier edges):

> **D = 3 + N/50 ;  target = round(N / D)**   (N = tool-call count)

It passes through the intuition (divisor ≈ 3 small, 4 at N=50, 5 at N=100, …), is
monotonic, and saturates toward ~50 so very long traces don't scale linearly:

| N | 20 | 45 | 60 | 100 | 150 | 300 | 1000 |
|---|----|----|----|-----|-----|-----|------|
| target | 6 | 12 | 14 | 20 | 25 | 33 | 43 |

The `3` (small-trace divisor) and `50` (saturation rate) are knobs. The target is a
**soft hint** to the agent, not a hard count.

## Step 3 — The segmenter agent (pi, cheap model; designed)

- **Input:** the `simplified_trace.txt`, the target (`"aim for ~T episodes"`), and a
  **worked few-shot example** of a simplified trace split into episodes-with-summaries
  — [`skillrace/fewshot/segmenter_example_input.txt`](../../skillrace/fewshot/segmenter_example_input.txt)
  + [`segmenter_example_output.json`](../../skillrace/fewshot/segmenter_example_output.json)
  (a `build-python-cli` run whose path forks on what the environment already provides —
  a dependency present vs missing, a data file present vs not; different domain on
  purpose). The granularity lesson (decision-density) and the environment-branch lesson
  both live in that example, not just prose.
- **Tools:** `read`/`bash` over the trace file. **No Docker, no container** — this is
  trace-only, which keeps it cheap.
- **Procedure (for long traces):** read the head first, commit early episodes, move
  forward; never try to hold the whole file at once.
- **Granularity instruction:** "Group consecutive tool calls that serve one sub-goal.
  Start a new episode where your *reasoning* shifts goal or makes a **contingent
  decision** (a choice specific to this task that could have gone otherwise — a font/
  layout/data choice, a diagnosis, a fix). Low-decision stretches (bulk reading) stay
  one episode even if long; decision-rich stretches split fine. Aim for ~T total."
- **Output (`episodes.raw.json`), the ONLY thing it produces** — a list of:
  ```json
  { "start_call": 12, "end_call": 13,
    "intent": "establish the bespoke type + token system",
    "what_it_did": "wrote a custom tailwind config (Cormorant Garamond / DM Sans, display scale) and global CSS",
    "outcome": "wrote tailwind.config.js (3683 bytes) and index.css" }
  ```
  It does **not** author `opening_reasoning` — that is attached verbatim by the
  assembler (below) to avoid paraphrase drift on the guard signal.

It runs via pi like [`gen_agent.py`](../../skillrace/gen_agent.py): an agent does the
fuzzy work and **writes a JSON artifact**; our code validates and assembles.

## Step 4 — Deterministic assembler (designed)

Reads `episodes.raw.json`, validates, and emits the per-run `episodes.json`:

- **Validate** the spans: in range `1..N`, sorted, contiguous, no gaps/overlaps
  (they **partition** the tool calls); every episode carries a summary. On failure,
  one **repair** round-trip to the agent with the specific error; then flag
  `unsegmentable` and count it (never silently accept a partial split).
- **Attach `opening_reasoning`** to each episode: the **verbatim** `reasoning` of the
  turn that owns the episode's `start_call`.
- **Build the chain:** nodes = episodes (their summaries); the **edge** from episode
  *i*→*i+1* is *i+1*'s `opening_reasoning`.

---

## Edges — why `opening_reasoning` matters

> If Ep1 = calls 1–3 and Ep2 = calls 4–8, the **reasoning of the turn owning call 4**
> is the edge pointing Ep1 → Ep2.

That edge is exactly the signal the **Guard Extractor**
([guard-synthesizer.md](./guard-synthesizer.md)) negates to synthesize a divergent
test ("the next episode's opening reasoning" is a named guard source). Keeping it
**verbatim** (assembler-attached, not agent-paraphrased) preserves the signal. A
per-run chain becomes a **tree** only when [merged with other runs](./tree-builder.md):
shared prefixes collapse, divergences branch.

---

## Granularity principle (the heart of the revision)

Episodes are sized by **decision density**, not uniform length:

- a 9-call bulk-read of the codebase = **one** episode (no contingent choice);
- a single `write` that commits the page's type system = **its own** episode;
- a `verify` that **fails** and the `fix` that follows = separate episodes, because the
  failure outcome is a branch point.

Coarse "phase" episodes (orient→read→build→verify) are identical across all tasks and
produce identical guards; decision-level episodes are what let the concolic loop
generate genuinely different tests.

---

## Tradeoff (honest)

Running via pi means **default temperature, non-deterministic** (Pi exposes no
temperature — OQ-1), softening the original determinism guarantee. Accepted because:
the agent uses a **cheap model**, reads text, needs **no Docker**, and emits tiny
JSON — far below the agent-under-test's cost; and the deterministic assembler +
validation catch malformed output. Cache by `simplified_trace.txt` hash for replay.

---

## How to test it in isolation

- **Renderer (pure code):** golden `simplified_trace.txt` for a fixed `session.jsonl`;
  assert truncation, global numbering, no-duplication, `result(ERROR)` marking.
- **Target (pure code):** assert the table above; assert monotonic and saturating.
- **Assembler (pure code, stubbed agent JSON):** feed a valid split → assert spans
  partition `1..N` and `opening_reasoning` matches the owning turn verbatim; feed a
  **gap/overlap/out-of-range** split → assert the validator rejects it and the
  repair/flag path engages (never a silent partial).
- **Segmenter agent (recorded run):** keyed by `simplified_trace.txt` hash, offline.

---

## Failure modes

| Situation | Behavior |
|-----------|----------|
| Agent JSON spans have a gap/overlap or are out of range | Validator rejects → one **repair** round-trip with the error → then `unsegmentable`, run counted, not folded. |
| Agent omits a summary for an episode | Same repair/flag path. |
| Trace too long for one read | Agent pages head-first (its instructed procedure); only spans + summaries come back. |
| Empty trace (0 tool calls) | `episodes: []`; downstream treats it as a no-op fold. |
| pi/provider error | Surfaced to the caller as retryable; not swallowed. |

**Surfacing:** a trace that can't be cleanly segmented is **marked, counted, and
reported** — never silently half-segmented.
