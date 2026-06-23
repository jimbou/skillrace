# Component 3 — Episode Summarizer

> **REVISED design (supersedes the original tex-derived spec).** Status: not yet
> implemented. **Summarization is now FUSED into the segmenter agent** — the same pi
> call that decides episode boundaries also writes each episode's summary (see
> [episode-segmenter.md](./episode-segmenter.md)). This doc owns the **summary schema**
> and the **outcome-grounding rule**; the segmenter doc owns the mechanics.

---

## What changed from the original spec, and why

| Original | Now | Why |
|---|---|---|
| A **separate component**: one **direct temp-0 model call per episode** | **Fused** into the segmenter agent's single pass | The agent has already read each episode to find its boundary; a second per-episode call is wasted cost. |
| Hard mechanical gate: `result_grounding.evidence` must be a verbatim **substring** of an `observation`, or reject | **Soft**: the rendered tool results are the ground truth the agent is shown and told to source `outcome` from; no separate evidence field | Matches how the rest of the system was built (the property checker likewise trusts the model to ground itself and stays auditable rather than rigid). |
| Rigid `{attempted, target, result}` template | Open fields `{intent, what_it_did, outcome}` | Less ceremony; same information, easier for the agent to fill in one pass. |

---

## ⚠️ The one rule that did NOT change

> **An episode's `outcome` is read from the episode's TOOL RESULTS (the observations),
> NEVER from the agent's narration (its `reasoning`).**

This is correctness-critical. Agents declare false victory ("great, the build passes!"
when it errored). If `outcome` came from the agent's claims, the skill would be
**grading itself**, and a run that ignored a real failure would be recorded as a
success — silently invalidating every property verdict downstream. The whole value of
the system (e.g. the `reduced-motion` finding, or catching the ceramicist run's real
vite build error) rests on outcomes reflecting **what actually happened**, not what the
agent thought happened. Preserving that divergence — observed outcome vs. narrated
claim — is itself a **bug signal** the guard extractor later consumes.

The simplified trace makes this enforceable: tool **results** are rendered explicitly
(and `result(ERROR):` when pi flagged `isError`), so the agent is shown the ground
truth and is told to source `outcome` from it.

---

## The summary schema (per episode)

Authored by the segmenter agent into `episodes.raw.json`; `opening_reasoning` is added
**verbatim** by the deterministic assembler (not the agent — to keep the guard signal
undistorted):

```json
{
  "index": 7,
  "start_call": 20,
  "end_call": 23,
  "intent": "verify the page renders",
  "what_it_did": "started the vite dev server and checked it served",
  "outcome": "BUILD FAILED — vite reported a syntax error in products.js",
  "opening_reasoning": "Let me run the dev server and curl it to confirm it renders."
}
```

- **`intent`** — the episode's sub-goal (a few words).
- **`what_it_did`** — the actions, in one line.
- **`outcome`** — *the result, read from the tool results.* Where the correct result is
  computable, it states the concrete outcome (failed/passed, the error, the HTTP
  status), not a vague "ran the tests." The grounding lives in the `outcome` text
  itself, sourced from the rendered tool results the agent was shown.
- **`opening_reasoning`** — assembler-attached; the reasoning of the turn owning
  `start_call`. It is the **edge** into this episode and the guard signal — kept
  verbatim. (See [episode-segmenter.md](./episode-segmenter.md#edges--why-opening_reasoning-matters).)

A trivial code join over the segmentation spans + these summaries produces the per-run
[`Episode`](../data-contracts.md#5-episode--the-trees-atom-segmentation--summary)
chain.

---

## How outcome-grounding is enforced (softly)

1. **Prompt**: the agent is told `outcome` MUST describe what the **tool results** show
   (exit codes, printed text, errors, HTTP status) — explicitly NOT to use its own
   `reasoning` as the outcome.
2. **Rendering**: results are shown verbatim (truncated) and errors are flagged
   (`result(ERROR):`), so the ground truth is directly in front of the agent.

The summarizer can fail open in exactly one explicit, counted way:
`outcome: "no observable outcome"` for an episode whose calls produced no result. It
must **never** substitute narration for an observed outcome.

---

## How to test it in isolation

Recorded-agent fixtures over a fixed `simplified_trace.txt`:

- **`pass_episode/`** — results show success → `outcome` reflects it.
- **`fail_episode/`** — the ceramicist vite error above → `outcome` reflects the build
  failure (the `✘ ERROR` line).
- **`false_victory/` (defining test)** — an episode where the **result** shows a failure
  but the **reasoning** says "all good." Assert `outcome` reflects the **failure** and
  the narration claim does NOT leak in.
- **`no_observation/`** — an episode of pure reasoning/edits with no informative result
  → `outcome: "no observable outcome"`, never a reasoning-derived one.

---

## Failure modes

| Situation | Behavior |
|-----------|----------|
| `outcome` not traceable to any tool result in the span | Flag `outcome_ungrounded`; count it; downstream treats it conservatively. Never silently accepted as grounded. |
| Episode genuinely has no tool result (all reasoning/edits) | `outcome: "no observable outcome"` — correct, not an error. |
| Decisive result line was truncated | Outcome reads from what remains; the truncation marker signals possible loss (rare). |
| Agent returns malformed summary JSON | Handled by the segmenter's validate → repair → flag path (one shared mechanism). |

**Surfacing:** ungrounded outcomes are visible in run stats, never hidden. Grounding in
the tool results is the property that makes every later verdict trustworthy.
