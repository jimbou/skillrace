# Property checker — blinded post-run Python checks

> **Implementation:** `skillrace.check_properties`, with checker authoring in
> `skillrace.compile_checks`. The approved design is
> [the July 15 checker design](superpowers/specs/2026-07-15-post-run-path-only-python-checkers-design.md).

The active RQ1 evaluator turns each natural-language property into a small Python
program after the coding agent finishes. This is intentionally a simple generated
oracle, not a production validation service and not a formal proof of correctness.

## Active campaign order

```text
candidate sanity → coding agent → immutable final snapshot
                 → path-only Python authoring → isolated execution → verdicts
```

All Random, VeriGrey-inspired, and SkillRACE executions use this exact evaluator path.
The checker author is not told which method produced the run.

For each property, the author receives only:

- the task prompt and generated environment description;
- the skill name, property ID/text, and `state`/`trace` hint;
- available tool names; and
- relative file paths in the final `/workspace` tree.

It does not receive file contents, command output, trace contents, workspace diff,
agent result, verdicts, method/source label, or campaign feedback. A trace-oriented
checker may read `/check/trace.jsonl` only when the frozen program executes. The active
post-run path does not expose `/check/workspace.diff`.

Because the final path tree depends on the run, this is reported as a **blinded
post-run path-adaptive generated oracle**, not an independent pre-run oracle.

## Python contract

Each generated artifact is a standalone Python 3 program:

- exit `0`: property holds;
- exit `1`: property is violated;
- exit `2`: cannot determine or checker-internal failure.

Timeouts, staging failures, unavailable Python, signals, and unexpected exit codes are
also not considered. They never count as an agent defect. A missing artifact required
by the task is a violation; if a genuinely conditional property's precondition is
absent, that conditional property holds.

The v2 prompt forbids inventing callable signatures, CLI syntax, input formats, CSV
headers/order, bounds, or expected values. It asks the generated program to inspect
runtime documentation, source, or `--help` before invoking an unfamiliar artifact and
to exit 2 when the exact expectation remains underdetermined. This generic rule was
added after both GLM-4.7 and DeepSeek-V3.2 v1 checks produced false violations from
guessed interfaces.

Python source is syntax-compiled before use. One syntax failure receives exactly one
retry containing the compiler error and previous source. If that retry is still
invalid, or authoring otherwise fails, only that property is excluded as not
considered. There is no semantic-audit model call and no generated Bash in the active
RQ1 path.

## Isolation and outputs

The evaluator snapshots the finished container once. Every valid checker runs in a
fresh `--network=none`, capability-dropped child with a host timeout, so one property
cannot prepare state for another. Fixed model-free checks still run. Human-authored RQ3
hidden Bash checks retain their existing independent path and are not relabelled as
generated Python checks.

```text
runs/<run>/
  post-run-check-input.json
  checks/
    manifest.json
    <property-id>.py
  verdicts.json
```

`manifest.json` records schema/provenance, prompt and policy versions, final snapshot
identity, path-tree hash, properties, model, available tools, script hashes, excluded
properties, per-call operation/receipt identities, input/output/cache-read tokens,
provider-credit cost, and unknown-cost status. Its fingerprint binds all inputs that
can affect generation. A cached checker is reused only when the fingerprint and script
hashes match.

The author call has no configured output-token ceiling and has a 120-second wall-clock
timeout. Calls use the durable Yunwu journal with tag `check.python.author`; their usage
and cost are included in campaign accounting. An outcome-unknown paid call stops the
campaign rather than being silently retried.

## Legacy compatibility

`compile_case` and `--author-post-hoc` remain readable for old development artifacts,
and RQ3 hidden checks remain human-authored Bash. They are not used by new RQ1 campaigns
and must not be presented as the active oracle design.
