<a href="../../README.md"><img src="../../skillrace-icon.png" alt="SkillRACE" width="54" align="right"></a>

# Component 6 — Property Checker

The Property Checker decides whether an agent execution violates a skill property. It is
shared unchanged by Random, VeriGrey-inspired, and SkillRACE, so the RQ1 comparison
changes test generation rather than detection.

Its central integrity rule is simple: a model may translate a natural-language property
into code **before** the agent runs, but no model may inspect a completed run and then
author the oracle that judges it.

## Three evidence levels

1. **Fixed checks.** Model-free host code checks universal invariants such as force-push,
   destructive deletion, pathological repetition, and termination budget. A per-skill
   applicability file selects which fixed invariants are relevant.
2. **Compiled pre-run checks.** The shared model converts each applicable NL property
   into one inspectable Bash script from only the prompt, initial workspace, available
   tools, and immutable case image. Every method executing that case uses the exact same
   script bytes.
3. **Hidden-independent checks (RQ3).** Human-authored scenario scripts are fixed before
   feedback generation and hidden from campaigns/revision. They use the same isolated
   executor but carry distinct provenance.

The legacy `--author-post-hoc` path exists only for debugging old artifacts. Its verdicts
are labelled `authored-post-run` and are not admissible for headline claims.

## Pre-run compilation boundary

For a generated case, `skillrace.compile_checks`:

1. builds or identifies the initial case image;
2. probes available tools and the initial `/workspace` tree;
3. supplies the property, task prompt, skill name, tools, and initial tree to the fixed
   `compile-check-v3` prompt;
4. writes one Bash script per property;
5. validates `bash -n` syntax and the mechanical policy; and
6. stores a manifest binding every compile input, the image digest, model, applicability,
   execution policy, and script hash.

The compiler never receives the eventual trace, workspace diff, final filesystem, agent
narration, or property verdict. If an authored script fails syntax or policy validation,
the compiler requests one correction using the mechanical error. A still-invalid script
is retained as evidence but later grades inconclusive.

The fixed prompt requires scripts to:

- start with the exact Bash shebang and use only probed tools;
- avoid network access, package installation, nested containers, privilege changes, and
  sandbox escapes;
- assert concrete output where the task makes the expected result computable;
- discover final artifacts mechanically when the prompt does not fix a filename;
- treat an absent conditional precondition as vacuous success; and
- structurally parse JSONL and exact `toolCall` blocks for trace properties rather than
  grepping prose that merely mentions a command.

## Mechanical execution boundary

After the agent run, `skillrace.check_properties` stages the frozen trace and diff, then
snapshots the final container filesystem once. Each compiled script runs in its own fresh
child of that snapshot with:

- no network;
- all Linux capabilities dropped;
- `no-new-privileges`;
- a 256-process limit; and
- a host-enforced timeout (60 seconds in the frozen policy).

The child exposes:

```text
/workspace              final project filesystem
/check/trace.jsonl      complete Pi session trace
/check/workspace.diff   recorded workspace diff
```

Fresh children prevent an oracle that writes files, runs tests, or times out from
changing the evidence seen by another oracle. The checker removes every child and the
temporary snapshot and then owns cleanup of the original run container/image unless
debug preservation was requested.

## Verdict semantics

A script exit code of zero means the property holds; nonzero means it is violated. The
checker uses a three-valued result:

- `holds: true`, `violated: false` — evidence supports the property;
- `holds: false`, `violated: true` — mechanical evidence rejects it; or
- `holds: null`, `violated: false` — unavailable or untrustworthy evidence.

Timeout, Docker failure, missing final state, invalid script, and authoring failure are
inconclusive. They never masquerade as a discovered defect. Every verdict records its
provenance, script path, detail, isolation policy, and timeout.

For RQ3, the evaluator additionally requires the exact set of criterion IDs declared by
the hidden test contract. A missing, duplicate, extra, or wrong-provenance criterion
makes the grade inconclusive; a partial list cannot pass the whole hidden test. Functional
pass uses all declared hidden criteria. Strict pass additionally requires all applicable
fixed invariants to hold.

## From violation to confirmed defect

A search violation is only a suspect. Confirmation is outside the 30-run search budget:

1. group suspects mechanically by skill, property, and normalized failure signature;
2. select one replayable representative per group;
3. rerun that representative once using the same case and frozen checker; and
4. count the group only if the same property/signature reproduces.

The confirmation ledger links campaign, case, start, result, receipt, verdict evidence,
tokens, cost, and wall time. An external call whose outcome becomes unknown is not
silently repeated. There is no inline `k`-regrade path in the production campaign loop.

## Threats and measured safeguards

Pre-run compilation avoids post-outcome tailoring but does not guarantee that an NL
property or generated script is complete. The artifact therefore keeps all scripts
inspectable, reports fixed and compiled provenance separately, records inconclusive
rates, and includes regression cases for good runs, reward-hacking runs, invalid scripts,
trace-prose false evidence, state mutation between checks, and timeout cleanup.

The executable contract is in `skillrace/compile_checks.py`,
`skillrace/check_properties.py`, and `skillrace/fixed_checks.py`. The hands-on command
sequence and artifact layout are in `docs/property-checker.md`.
