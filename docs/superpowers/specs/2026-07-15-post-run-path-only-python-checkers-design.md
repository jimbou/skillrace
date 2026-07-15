# Post-run Path-Only Python Checkers Design

## Goal

Replace the unreliable pre-run Bash checker plus same-model semantic-audit pipeline
with one simple post-run Python-checker generation step. The authoring model may use the
final workspace's paths to locate artifacts, but it must not see artifact contents,
command output, test results, verdicts, method identity, or campaign feedback.

## Why this design

The live pilots showed that Bash generation encouraged embedded Python, heredoc and
quoting failures, while a same-model semantic audit both accepted invalid GLM scripts
and rejected every usable DeepSeek script. Manual checker templates would be reliable
but would substantially narrow task coverage. A path-only post-run prompt is the small
middle ground: it resolves actual artifact names without exposing the answer contained
in those artifacts.

## Data flow

The agent finishes first. Before any property verdict is computed, the evaluator takes
one immutable snapshot of the final container and collects only the relative file and
directory paths under `/workspace`. It also probes the tools available in that snapshot.

For each natural-language property, the checker author receives:

- the original task prompt;
- the generated environment description already stored in candidate provenance;
- the property ID, natural-language text, and evidence-kind hint;
- the final workspace path tree;
- the available tool names; and
- the fixed checker interface described below.

The checker author does not receive file contents, the workspace diff, trace contents,
agent stdout, previous checker output, verdicts, method name, source arm, or behavioral
feedback. For a trace-scoped property, the generated checker may read
`/check/trace.jsonl` when it executes, but the author does not see that trace while
writing the checker.

The resulting Python source is compiled mechanically. On syntax failure, one and only
one targeted rewrite receives the syntax error and previous source. If the rewrite is
still invalid or either authoring call fails, that property is recorded as not
considered and evaluation continues. There is no semantic-audit model call.

Each valid source file is frozen with its hash and executed in its own fresh,
networkless child of the same final-state snapshot. Thus every property sees identical
agent output and cannot contaminate another property's evidence.

## Checker interface and verdicts

The generated artifact is a standalone Python 3 program. It may inspect or execute
files below `/workspace` and may read `/check/trace.jsonl` for a trace property. It must
not use the network, install software, invoke Docker, or modify trusted evidence.

Exit status has one fixed meaning:

- `0`: the property holds;
- `1`: the property is violated;
- `2`: the checker cannot determine the property or suffered an internal checker error.

The evaluator also treats timeouts, signals, unavailable Python, staging failures, and
unexpected exit codes as not considered. Checker failure is never an agent violation.
The checker should emit one short diagnostic line, which is stored with the verdict.

Missing task-required artifacts are violations, not vacuous success. A conditional
property whose actual precondition is absent holds. These rules are stated directly in
the author prompt; there is no second model asked to reinterpret them.

## Fairness and reproducibility

The same author model, prompt version, timeout, single-retry rule, Python interface,
isolation policy, and omission rules apply to Random, VeriGrey-inspired, and SkillRACE.
The author is blinded to the method. The final path tree is result-dependent, so the
paper must describe this honestly as a blinded post-run path-adaptive generated oracle,
not as a fully independent pre-run oracle.

The case fingerprint continues to bind stable candidate inputs, while a post-run
checker manifest binds the final snapshot identity, path-tree hash, author request
identity, scripts, retry records, token/cache/cost receipts, exclusions, and execution
policy. Resuming reuses a checker only when those identities and script hashes match.

## Simplifications and removals

The implementation removes the semantic-audit call, semantic decisions and rewrites,
pre-run checker compilation from the executor, arbitrary generated Bash, Bash policy
heuristics, and change-scoped diff enforcement. Fixed universal checks and human-authored
RQ3 hidden checks are unchanged.

The legacy post-hoc Bash authoring flag may remain readable for old development
artifacts, but no new RQ1 campaign uses it. It is not silently relabelled as the new
Python path.

## Testing

Tests are written red first for:

1. authoring occurs after the run and receives paths but no contents, diff, trace,
   method, or verdict;
2. emitted source is Python and syntax validation uses Python compilation;
3. one syntax-guided retry is allowed and a second failure excludes only that property;
4. semantic-audit calls are absent;
5. exit `0`, `1`, and `2`, plus timeout and unexpected exits, map to holds, violated,
   and not-considered correctly;
6. each checker runs in a fresh final-snapshot child;
7. manifest/fingerprint and receipts bind the path tree, final snapshot, policy,
   scripts, tokens, cache reads, costs, and operation identities; and
8. Random, VeriGrey-inspired, and SkillRACE share the exact same evaluator path.

The focused checker, campaign, and identity suites must pass, followed by the complete
offline suite, artifact smoke test, Python compilation, and `git diff --check`. No paid
call occurs during this replacement unless the user separately requests a new bounded
validation after all offline gates pass.
