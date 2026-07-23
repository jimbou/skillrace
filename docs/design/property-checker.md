<a href="../../README.md"><img src="../../skillrace-icon.png" alt="SkillRACE" width="54" align="right"></a>

# Component 6 — Property Checker

The Property Checker decides whether an agent execution violates a skill property. It is
shared unchanged by Random, VeriGrey-inspired, and SkillRACE, so the RQ1 comparison
changes test generation rather than detection.

Its practical integrity boundary is: checker authoring happens after the run but receives
only task metadata, available tools, and final workspace paths. It cannot inspect file
contents, trace/diff contents, results, verdicts, or method identity.

## Three evidence levels

1. **Fixed checks.** Model-free host code checks universal invariants such as force-push,
   destructive deletion, pathological repetition, and termination budget. A per-skill
   applicability file selects which fixed invariants are relevant.
2. **Post-run path-only checks.** The shared model converts each applicable NL property
   into one inspectable Python program using the task/environment and final path tree.
   Every method uses the exact same generation and execution procedure.
3. **Hidden-independent checks (RQ3).** Human-authored scenario scripts are fixed before
   feedback generation and hidden from campaigns/revision. They use the same isolated
   executor but carry distinct provenance.

The legacy `--author-post-hoc` path exists only for debugging old artifacts. Its verdicts
are labelled `authored-post-run` and are not admissible for headline claims.

## Post-run authoring boundary

After a generated case has run, `skillrace.check_properties` and
`skillrace.compile_checks`:

1. snapshot the finished container and collect available tools plus relative workspace
   paths;
2. supply one property, task prompt, environment description, skill, tools, and paths to
   the fixed `post-run-python-check-v2` prompt;
3. write one standalone Python program per property;
4. compile the source locally and, on syntax failure, retry once with the compiler error
   and previous source;
5. exclude only that property if the retry remains invalid or authoring fails; and
6. store a manifest binding every authoring input, receipt, token/cost field, snapshot
   identity, path-tree hash, model, applicability, execution policy, exclusion, and
   script hash.

The author sees the final path names but not their contents. It also never receives the
trace, diff, agent narration/output, prior checker output, property verdict, campaign
feedback, or method/source label. There is no semantic-audit model call. Since paths are
result-dependent, the paper reports this as a blinded post-run path-adaptive generated
oracle rather than an independent pre-run oracle.

The fixed prompt requires programs to:

- avoid network access, package installation, nested containers, privilege changes, and
  sandbox escapes;
- treat missing task-required artifacts as violations;
- treat an absent genuine conditional precondition as holding;
- use exit `0`, `1`, or `2` for holds, violated, or not considered; and
- print one short diagnostic.

## Mechanical execution boundary

After the agent run, `skillrace.check_properties` stages the frozen trace, then
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
```

Fresh children prevent an oracle that writes files, runs tests, or times out from
changing the evidence seen by another oracle. The checker removes every child and the
temporary snapshot and then owns cleanup of the original run container/image unless
debug preservation was requested.

## Verdict semantics

A Python exit code of `0`, `1`, or `2` means holds, violated, or not considered. The
checker uses the corresponding three-valued result:

- `holds: true`, `violated: false` — evidence supports the property;
- `holds: false`, `violated: true` — mechanical evidence rejects it; or
- `holds: null`, `violated: false` — unavailable or untrustworthy evidence.

Timeout, Docker failure, missing final state, invalid source, authoring failure,
unavailable Python, and unexpected exit are not considered. They never masquerade as a
discovered defect. Every verdict records its
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
4. mark the group reproduced only if the same property/signature recurs; and
5. count it in headline yield only if the representative's independent patched-skill
   replay makes the exact case pass every originally failed property.

The confirmation ledger links campaign, case, start, result, receipt, verdict evidence,
tokens, cost, and wall time. An external call whose outcome becomes unknown is not
silently repeated. There is no inline `k`-regrade path in the production campaign loop.

## Threats and measured safeguards

Path-only blinding reduces direct answer leakage but does not formally guarantee that an
NL property or generated program is semantically correct. The artifact therefore keeps
all programs and receipts inspectable, reports fixed/path-only/hidden provenance
separately, records not-considered rates, and requires both unchanged-skill
reproduction and successful patched-skill exact-case replay before a generated-oracle
finding becomes a headline defect. Regression cases cover the two observed semantic
checker bugs, good runs, reward-hacking runs, invalid scripts, trace-prose false evidence,
staging permissions, state mutation between checks, and timeout cleanup.

The executable contract is in `skillrace/compile_checks.py`,
`skillrace/check_properties.py`, and `skillrace/fixed_checks.py`. The hands-on command
sequence and artifact layout are in `docs/property-checker.md`.
