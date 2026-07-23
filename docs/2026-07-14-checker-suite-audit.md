# Checker suite audit — 2026-07-14

## Scope and method

This is an offline, pre-experiment audit. It made no model or agent calls and inspected
no headline result because none exists.

The property inventory was taken from the 30 skills named by
`experiments/manifests/rq1-skills.draft.json` and the 10
`scenarios/*/campaign/properties.json` files. The review checked whether each property
is a must-hold skill/task requirement, whether it is conditional, whether a missing
artifact should fail, and whether a generated checker would need to discover a
task-specific interface.

The saved-script inventory examined every `checks/*.sh` under
`out/development-pilots`, `experiments/development-pilots`, and `scenarios`. Pattern
counts are triage only; each confirmed finding below was read manually.

## Property inventory result

- RQ1: 30 skills, 90 unique properties.
- RQ3 public phase: 10 scenarios, 30 unique properties.
- All property objects have nonempty unique IDs and supported evidence kinds
  (`state`, `trace`, or `state+trace`).
- Six specifications contain explicit conditional/task-dependent language. Five are
  true conditional properties: `json-parser/valid-json-out` and the four SQL
  `nulls-handled` properties. The TDD `test-written-and-failed-first` property uses an
  `if no evidence` sentence to define a violation, not a vacuous precondition.
- No property specification was changed. The reviewed requirements are supported by
  the corresponding skill or public-task contract. The observed P0 failures arose in
  generated scripts that changed or guessed those requirements.

The conditional properties need particular compiler care:

- `valid-json-out` applies only when the requested or produced interface emits JSON. A
  DataFrame/CSV/stdout-table task must not be converted into a JSON-output task.
- SQL NULL properties apply only when the question uses a relevant aggregate over data
  containing NULLs.
- Missing task-required source data, implementation artifacts, CLIs, parsers, or tests
  are failures of the main behavior properties. They are not absent conditional
  preconditions.

### RQ1 properties reviewed

| Skill | Property IDs |
|---|---|
| `fastapi-endpoint` | `endpoint-responds`, `builds-clean` |
| `cli-argparse-fix` | `does-what-asked`, `runs-without-error`, `bad-args-nonzero` |
| `cli-subcommand-validator` | `does-what-asked`, `runs-without-error`, `bad-args-nonzero` |
| `code-refactor-fowler` | `behavior-preserved`, `tests-unedited`, `actually-changed` |
| `condition-based-waiting` | `stable-suite`, `condition-synchronized`, `bounded-fresh-polling`, `timing-waits-justified`, `tests-not-weakened` |
| `debugging-difficult-bugs` | `bug-fixed`, `no-test-weakened` |
| `finishing-a-development-branch` | `no-failing-tests-committed`, `history-clean` |
| `json-parser` | `parses-valid`, `rejects-invalid`, `valid-json-out` |
| `parser-generator` | `parses-valid`, `rejects-invalid` |
| `refactor` | `behavior-preserved`, `tests-unedited`, `actually-changed` |
| `refactor-complexity-reduce` | `behavior-preserved`, `tests-unedited`, `actually-changed` |
| `regex-expert` | `validator-correct`, `anchored-no-partial-match`, `terminates-no-catastrophic-backtracking` |
| `sql-queries` | `answer-correct`, `query-executes-clean`, `nulls-handled` |
| `sql-query-generator` | `answer-correct`, `query-executes-clean`, `nulls-handled` |
| `sql-query-json` | `answer-correct`, `query-executes-clean`, `nulls-handled` |
| `sqlmodel-orm` | `answer-correct`, `query-executes-clean`, `nulls-handled` |
| `systematic-debugging` | `bug-fixed`, `no-test-weakened` |
| `test-driven-development` | `feature-suite-passes`, `test-written-and-failed-first`, `no-test-weakened`, `output-pristine` |
| `unit-test-generation` | `generated-tests-pass`, `tests-exercise-target` |
| `unit-test-generator` | `generated-tests-pass`, `tests-exercise-target` |
| `using-git-worktrees` | `work-isolated`, `no-uncommitted-mess` |
| `yaml-config` | `valid-config-accepted`, `malformed-rejected` |
| `network-config-validation` | `dangerous-commands-detected`, `address-conflicts-detected`, `section-scoped-validation` |
| `rest-api-caller` | `request-matches-contract`, `response-fields-correct`, `http-failures-reported`, `credentials-not-hardcoded` |
| `csv-workbench` | `numeric-summary-correct`, `csv-parsing-correct`, `source-data-preserved` |
| `argparse-scaffolder` | `cli-contract-correct`, `invalid-input-rejected`, `help-and-entrypoints-work`, `cli-suite-passes` |
| `data-transform` | `transformation-values-correct`, `missing-and-duplicate-policy`, `schema-and-row-identity-preserved`, `transform-artifact-reproducible` |
| `compiler-hardening` | `release-build-hardened`, `hardened-binary-verifies`, `sanitizer-target-effective`, `build-config-portable` |
| `validator-agent` | `valid-inputs-accepted`, `invalid-inputs-rejected`, `complete-input-consumed`, `validator-constraints-faithful` |
| `log-parser` | `log-filters-correct`, `malformed-lines-policy`, `csv-output-faithful`, `parser-reruns-without-source-mutation` |

### RQ3 public properties reviewed

| Scenario | Property IDs |
|---|---|
| `argparse-cli` | `cli-public-contract`, `cli-errors-and-help`, `cli-verification` |
| `config-parser` | `config-public-contract`, `config-invalid-input`, `config-verification` |
| `csv-stats` | `csv-stats-public-contract`, `csv-robust-parsing`, `csv-stats-verification` |
| `fix-failing-test` | `failing-suite-passes`, `tests-remain-intact`, `suite-was-verified` |
| `interval-merge` | `interval-public-contract`, `interval-boundaries`, `interval-input-integrity` |
| `json-csv` | `json-csv-public-contract`, `json-csv-roundtrip`, `json-csv-errors` |
| `log-parser` | `log-public-contract`, `log-malformed-lines`, `log-verification` |
| `regex-validate` | `regex-public-contract`, `regex-anchored`, `regex-terminates` |
| `sqlite-query` | `sqlite-answer-correct`, `sqlite-schema-aware`, `sqlite-verification` |
| `text-template` | `template-public-contract`, `template-literal-preservation`, `template-special-values` |

## Saved checker inventory

The inventory contains 284 scripts:

- 40 manifest-linked generated development checkers under `out/`;
- 52 older development scripts under `experiments/`; and
- 192 human-authored RQ3 hidden checks under `scenarios/`.

The historical handoff reported 21 missing-artifact-vacuity pattern matches and 12
stdout-as-JSON matches. A fresh deliberately broader line/window scan found:

| Pattern | Broad matches | Manual conclusion |
|---|---:|---|
| missing/empty artifact followed by nearby `exit 0` | 24 | 22 confirmed invalid missing-required-artifact paths; one distant false match and one legitimate absent-output conditional |
| explicit “vacuous” text | 29 | mixes invalid missing-required-artifact paths with valid conditional branches |
| stdout/output parsed as JSON | 16 | broad triage; includes 10 `valid-json-out` scripts plus unrelated JSON validation |
| guessed common callable-name lists | 2 | both confirmed in the saved `positive-gate-v6` json-parser candidate |
| fallback prints/copies the supplied test data | 1 | confirmed manufactured-output bug in `positive-gate-v6/valid-json-out.sh` |

The broader counts intentionally differ from the handoff's narrower regular expressions;
neither count is treated as a defect total.

No suspicious missing-artifact-vacuity match came from the 192 human-authored hidden
checks. The recurring problem is concentrated in model-generated development checkers.

## Confirmed generated-checker failure modes

1. Behavior scripts for parsers, validators, and CLIs repeatedly return success when the
   required implementation artifact is absent. This can reward an agent that produces
   nothing.
2. The saved `positive-gate-v6/parses-valid.sh` chooses from guessed names such as
   `parse_data`, `process_json`, and `main`, misses the actual `parse_sensor_data`, and
   assumes a data-object argument rather than inspecting the real interface.
3. The saved `positive-gate-v6/valid-json-out.sh` turns a DataFrame task into a JSON
   stdout requirement and contains a fallback that prints the test input itself.
4. Several other `valid-json-out` scripts inspect stdout or generic source patterns even
   when the task requests a DataFrame or CSV. These are triage findings; candidate-level
   semantic review must decide whether the conditional precondition is actually present.

## Gate decision

The property specifications themselves can remain. Previously generated development
checkers are diagnostics only and must not be promoted or reused. Fresh candidates must
use the new pre-run batch self-audit and final mechanical validation. The focused offline
test boundary must pass before any paid call, and the first bounded live candidate must
be manually reviewed again before its one permitted patch/replay chain.

## July 15 live follow-up

A GLM-4.7 development run exposed two additional general checker patterns. First,
`bash -n` returned zero while warning that a heredoc was unterminated; the resulting
embedded Python failed at evaluation time. Mechanical validation now rejects any Bash
parse warning and uses the existing one retry, then property exclusion. Second, a
change-scoped checker scanned the complete final test and blamed the agent for a
legitimate fixed delay already serving as the simulated event trigger. Change-scoped
properties must now inspect `/check/workspace.diff`.

A fresh scan of all 90 RQ1 properties found exactly three properties that trigger this
rule: `condition-synchronized`, `bounded-fresh-polling`, and
`timing-waits-justified`, all under `condition-based-waiting`. The broader phrase
“changed only as requested” in `data-transform/schema-and-row-identity-preserved` is a
final-state correctness requirement and deliberately does not trigger the diff rule.
All 30 RQ3 public properties remain unaffected.

## July 15 post-run Python redesign and re-audit

The Bash/self-audit design above is now historical. The active RQ1 path runs the agent
first and gives the checker author only task/environment metadata, available tools, one
NL property, and final workspace paths. It generates standalone Python, compiles it,
retries syntax once, and otherwise excludes that property. There is no semantic-audit
call and no diff in the active checker interface. Fixed checks and the human-authored
RQ3 hidden Bash checks are unchanged.

The full property inventory was re-read from the current draft manifests after this
change:

- RQ1 remains 30 skills and 90 properties; all IDs/text are nonempty, evidence kinds
  are supported, and IDs are unique within each skill.
- RQ3 remains 10 scenarios and 30 public properties with the same checks passing.
- The conditional-property conclusions above are unchanged, so no property text was
  edited to accommodate the new checker generator.

The saved Bash inventory has grown to 308 scripts: 64 under `out/development-pilots`,
52 under `experiments/development-pilots`, and 192 human-authored RQ3 hidden scripts.
The 24 additions are later July 14/15 diagnostics. Manual inspection confirms the same
general failure modes rather than a new specification problem: DeepSeek scripts treat
missing required test files/assertions as vacuous success, while the GLM waiting checker
contains the recorded malformed heredoc and final-tree/diff confusion. At this audit
point no post-run Python checker existed. The later live follow-up below adds eight
generated Python diagnostics. All historical scripts remain diagnostic-only and cannot
be reused as new oracle evidence.

## July 15 live Python-checker findings

Two fresh budget-one Random campaigns reached the v1 Python checker path:

- GLM-4.7 on `validator-agent` authored four syntactically valid checks. Its single
  reported violation was false: the checker invented newline-separated input although
  the validator contract required a space after `N`.
- DeepSeek-V3.2 on `log-parser` authored four syntactically valid checks and reported all
  four properties violated. Manual review rejected all four findings: one included the
  whole 16:00 hour instead of ending at 16:00, two invented an exact three-column CSV
  schema despite the record-preserving task, and one invented positional CLI arguments
  instead of the documented `-i/-o` interface.

Python and final path names remove Bash/heredoc failures but do not by themselves prevent
guessed interfaces. None of the five reported violations is eligible for repair or
headline evidence. A generic prompt-only v2 now requires generated programs to inspect
runtime documentation/source/help, forbids invented signatures/formats/headers/bounds,
and requires exit 2 when an expectation remains underdetermined. This is not a new audit
service. Focused offline tests pass; v2 did not receive a valid live end-to-end sample
because the next CSV campaign had one sanity rejection and one invalid/truncated
realization, exhausting its two pre-agent attempts.
