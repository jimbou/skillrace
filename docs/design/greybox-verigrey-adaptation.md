<a href="../../README.md"><img src="../../skillrace-icon.png" alt="SkillRACE" width="54" align="right"></a>

# Greybox rung — the concrete VeriGrey adaptation

> Instantiates [baselines.md](./baselines.md) Rung 2 against what VeriGrey
> (Zhang et al., arXiv:2603.17639) **actually does** (its Alg. 1–2, §4.1), so every
> design choice in our port is traceable to their paper. Goal: an adaptation no
> reviewer can call a strawman — each mechanism is either (a) their mechanism
> verbatim, (b) their mechanism with the injection-specific part swapped for the
> shared correctness component, or (c) a declared free parameter for which we
> report a sensitivity ablation and use the **best-performing** setting in the
> headline comparison.

---

## 1. Mechanism-by-mechanism mapping

| VeriGrey (published) | Ours | Category |
|---|---|---|
| Input = injection prompt `s` (user task `u` fixed) | Input = candidate `(prompt, env-tail)` | setting change (correctness testing has no attacker input; the *test input* is the whole candidate) |
| `InstrumentToolCalls`: record tool name + args per invocation (Alg. 2) | read tool calls from the pi session trace (name + args) — same information, no instrumentation needed | (a) verbatim |
| Tool-sequence database `D`; a **new sequence** ⇒ seed kept (Alg. 1 l.15) | identical: novelty index over schematized tool-event sequences; novel run ⇒ its candidate joins the seed corpus | (a) verbatim |
| `AssignEnergy`: +1 each for **new tool**, **new transition**, **new sequence**; energy = number of offspring (§4.1.2) | identical, same three increments, energy = offspring count for that seed | (a) verbatim |
| `ChooseSeed`: seeds that produced novelty get greater opportunity | identical: sample seeds weighted by their accumulated novelty score (recency-tie-broken FIFO otherwise) | (a) verbatim |
| `MutatePrompt`: LLM mutation with **context bridging** (link injection task to user task, feed back failed prompt + its tool sequence) (§4.2, Fig. 3) | LLM mutation of the seed's `(task_nl, env_nl)`, feeding back the seed's tool sequence ("this input produced this behavior; produce a variant likely to elicit *different* tool behavior"). Context bridging itself is **inapplicable** — there is no second (injection) task to bridge to | (b) swap: keep "LLM mutates, conditioned on the observed tool sequence"; drop only the injection-specific bridge |
| `IsInjectionTaskSuccessful` oracle | the **shared property checker** | (b) swap (their oracle is injection-specific by construction) |
| Verifier/conversation agent (C3) | not applicable (our runs are single-shot tasks, as in our whole pipeline) | (b) |
| Budget: N agent executions per campaign (N=100 in their eval) | identical: same run budget as all rungs | (a) |

**What greybox never sees (by definition — this is the ablation):** the agent's
`reasoning`, episode segmentation, outcomes, the behavior tree, guards,
properties-at-selection-time. It reads only tool names + args of finished runs.

**What greybox shares with every rung (fairness controls):** the runner, the base
images, the run budget, the realize→build→model-repair pipeline (a candidate that
doesn't build costs a repair, not an agent run — same as SkillRACE's validator
gate for buildability), the same generator/judgment model at the same
temperatures, and the same per-case property checks.

---

## 2. The one real free parameter: tool-event label granularity

VeriGrey's labels are `(tool_name, args)` (Alg. 2 l.3). In their setting (AgentDojo
workspace/banking tools) args are drawn from a small space, so raw-args labels are
meaningful. In a coding agent, raw args (full bash commands, file paths, file
contents) are near-unique per run — raw-args labels would mark **every** run novel,
degenerating the feedback to "keep everything" (an unfairly weakened port).
Conversely, name-only labels may be too coarse. Neither extreme is "the" faithful
port; this is a genuine adaptation decision, so we treat it the way one should:

- **L0 — name only:** `bash`, `read`, `write`, `edit`, …
- **L1 — name + schematized arg head:** `bash:pytest`, `bash:npm`, `read:.py`,
  `write:.ts`, `edit:.json` (first command token for bash; target extension for
  file tools).
- **L2 — name + normalized arg:** L1 plus a normalized path bucket
  (`write:src/*.ts`, `bash:pytest tests/*`).

Novelty (sequence / transition / tool) is computed over the chosen label alphabet.
**We run the granularity ablation on a subset of skills, report all three, and use
the best-performing level for the headline greybox numbers.** This is the strongest
possible answer to an unfairness objection: the baseline got its best
configuration, chosen empirically, and the sensitivity is in the paper.

---

## 3. Algorithm (drop-in `Generator`)

```
state: corpus = [seed candidates + their runs]     # seeded by the shared seed phase
       D_seq, D_trans, D_tool = novelty sets over schematized labels
       queue = seeds with pending energy

fold(candidate, run_dir):                          # code only, no model
    seq = schematize(tool_calls(run_dir), level=L)
    new_tool  = any(t not in D_tool for t)
    new_trans = any(e not in D_trans for e)
    new_seq   = seq not in D_seq
    update D_*
    energy = new_tool + new_trans + new_seq        # VeriGrey §4.1.2, verbatim
    if energy > 0: corpus.add(candidate, seq); queue.push(candidate, energy)

propose():                                         # one model call, like Rung 1
    seed = choose_seed(queue)                      # novelty-weighted
    (task', env') = llm_mutate(seed.task_nl, seed.env_nl, seed.tool_seq)
    return realize_and_build(task', env')          # SHARED realize+repair pipeline
```

The mutation prompt mirrors VeriGrey Fig. 3 minus injection framing: it shows the
seed's task/env, the tool sequence its run produced, and asks for a variant of the
task/environment "likely to drive the agent through different behavior," at the
same temperature as the floor's proposer.

---

## 4. What the paper must state (honesty box)

1. This is VeriGrey's **feedback and scheduling mechanism**, verbatim; its
   **injection-specific** mutation bridge and oracle are replaced (they are
   meaningless outside prompt-injection testing). The comparison therefore reads:
   *tool-sequence-novelty feedback vs. reasoning-guard mutation, for correctness
   testing* — not a claim about VeriGrey's security performance.
2. Label granularity is an adaptation parameter; we report the sensitivity and use
   the baseline's best setting.
3. Structural expectation (to be tested, not assumed): tool-sequence novelty
   rewards *any* behavioral difference, property-relevant or not; in correctness
   testing most novel sequences are benign variation, whereas guard mutation
   targets the conditions properties depend on. If greybox nevertheless wins
   somewhere, that result stands and gets analyzed.

---

## 5. Isolation tests

- `greybox_reads_no_reasoning`: feed a trace whose reasoning field is poisoned with
  a sentinel; assert the generator's state and prompts never contain it.
- `novelty_index_pure`: fold is code-only (ForbiddenModel), deterministic, and the
  three energy increments fire on crafted traces (new tool / new transition / new
  sequence) exactly as in VeriGrey §4.1.2.
- `granularity_levels`: same trace pair is novel under L0/L1/L2 as hand-computed.
- `shared_pipeline`: propose() calls the same realize/build/repair entry points as
  Rung 1 (spy).
