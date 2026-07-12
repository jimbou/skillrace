<a href="../../README.md"><img src="../../skillrace-icon.png" alt="SkillRACE" width="54" align="right"></a>

# Evaluation methods and fairness boundary

This document defines the three complete systems in the RQ1 comparison. The approved
protocol—not an individual CLI flag—owns their model, budget, bootstrap allocation,
candidate realization, sanity gate, runner, property checker, and accounting rules.

The comparison contains exactly:

1. independent Random generation;
2. a VeriGrey-inspired tool-sequence greybox baseline; and
3. full SkillRACE concolic execution.

There is no seeded black-box arm, outcomes-only arm, direct-property baseline, model
sweep, or per-skill choice of the strongest Greybox configuration.

## Shared experimental boundary

For one method/skill campaign, every system receives exactly 30 counted agent
executions with `qwen3.6-flash`. Every candidate uses the same realization/build/repair
pipeline, basic mechanical sanity gate, Pi runner, pre-run property compiler, fixed
checks, and isolated mechanical oracle execution.

Build, schema, or pre-agent sanity rejection is a generation failure and does not consume
one of the 30 agent executions. Once Pi is recorded as started, success, timeout, agent
error, and oracle-inconclusive outcomes consume a slot. Attempts and failures remain in
the durable campaign manifest.

The methods have isolated output/search state. They may receive different independently
generated inputs because the algorithms need different initialization; fairness comes
from the same frozen sampling protocol and equal expensive-agent budget, not from forcing
all systems to share a favorable seed corpus.

## Random: 0 bootstrap + 30 fresh tests

Random is the feedback-free black-box floor:

- generate a fresh task/environment independently at every counted iteration;
- use a digest of prior descriptions only to discourage literal duplicates;
- never select or mutate a previous execution;
- never read traces, tool calls, reasoning, outcomes, episodes, properties, guards,
  trees, or verdicts when generating the next test; and
- ignore completed executions except for protocol accounting and result storage.

Random therefore has no seed tree or initialization corpus. Calling its 30 independent
draws “seeds” would obscure the algorithm and is avoided in the paper and artifact.

## VeriGrey-inspired: 10 bootstrap + 20 guided tests

This baseline adapts VeriGrey's feedback mechanism to correctness testing while clearly
disclosing the non-transferable parts:

- independently generate and execute ten diverse initial cases;
- retain all ten cases, including cases with duplicate schematized sequences;
- initialize coverage from every bootstrap execution before mutation starts;
- expose only globally frozen L1 tool events and derived tool/transition/full-sequence
  novelty to the search policy;
- give a candidate one energy unit for each new tool, transition, and full sequence;
- select a retained case by that novelty/energy state and ask the shared model for a
  mutation conditioned on its tool sequence; and
- spend the remaining 20 counted executions on those guided mutations.

The generator cannot read reasoning, episode purposes, outcomes, correctness properties,
guards, SkillRACE tree state, or verdicts. One global L1 event schema is fixed before
headline results; the experiment does not choose L0/L1/L2 per skill.

The name is “VeriGrey-inspired” because VeriGrey's injection-specific context bridging,
mutation operators, and injection oracle do not transfer to coding-skill correctness.
The artifact adapts the published feedback/scheduling idea and replaces only those
domain-specific pieces with the same shared realization and correctness oracle used by
all three systems.

## SkillRACE: 10 bootstrap + 20 concolic tests

SkillRACE independently generates and executes ten bootstrap cases, converts concrete
runs into episodes, folds them into a behavior tree, extracts reasoning/outcome-derived
branch conditions, and spends 20 further executions seeking property-relevant unexplored
situations.

Its mutation policy is deliberately opportunistic:

- a mutation may change several coherent environment features;
- reaching the intended branch is recorded, not required;
- a different new branch is useful exploration;
- every reproducible property violation remains eligible regardless of its motivating
  target; and
- targeted, serendipitous, path-miss, alternate-new-branch, and no-divergence labels are
  mechanism measurements derived from these same headline executions.

This preserves the paper's concolic-execution framing without claiming exact symbolic
constraint solving or requiring the model's stated reason to be causal.

## Parallel execution without semantic drift

Random and VeriGrey-inspired proposals are transactionally reserved in batches. For a
SkillRACE epoch, one reducer freezes the current tree and a deterministic, branch-diverse
target plan before workers synthesize anything. Workers write immutable results; the
reducer folds them in deterministic candidate-ID order, independent of completion order.
Global API, Docker, and agent semaphores bound resource use. Durable lifecycle receipts
and fold progress permit exactly-once resume after interruption.

Parallel execution is an implementation optimization, not another experimental arm.

## Required regression evidence

The artifact tests assert that:

- the protocol enforces `30/0+30`, `30/10+20`, and `30/10+20`;
- role-specific model overrides are rejected;
- Random and VeriGrey-inspired cannot access forbidden feedback;
- all VeriGrey bootstrap cases initialize coverage and only later mutants face novelty
  retention;
- duplicate coordinates cannot be reserved or executed twice;
- a started-but-unknown execution is charged conservatively rather than replayed;
- reversed worker completion produces byte-identical folded SkillRACE state; and
- confirmation reruns and defect grouping occur after search, outside the 30-run budget.

The source of truth for the full study is
`docs/superpowers/specs/2026-07-11-skillrace-evaluation-design.md`; the checked-in
machine protocol is `experiments/protocols/issta-main.draft.json` until the final
pre-result freeze.
