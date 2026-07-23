# SkillRACE session handoff — 2026-07-14

This is the stopping record for the long implementation/debugging session ending on
2026-07-14. It records what is actually implemented, what the paid development runs
showed, what remains unsafe, and the order in which work should resume. Development
outputs below are diagnostics only. No headline RQ1 or RQ3 experiment has been run.

## 1. Current bottom line

The guided SkillRACE patch-only backend now works mechanically from saved evidence to an
immutable patched skill. Its output can be replayed independently and consumed by the
strict RQ1 verifier. One genuine saved-failure chain exercised:

```text
saved campaign failure
  → method-appropriate repair evidence
  → guided Pi patch-only execution
  → immutable patch receipt
  → independently launched exact replay
  → immutable confirmation receipt
  → verified bounded-development RQ1 cell
```

The chain completed, but the replay reproduced the same failure. It therefore produced
zero confirmed defects, which is the correct conservative result. Inspection then showed
that the selected development failure was caused by invalid generated checkers rather
than a demonstrated skill defect. The continuation on the same date implemented and
offline-verified the minimal semantic checker self-audit described in Section 7. The
remaining live blocker is the absence of an eligible current-format saved failure; do
not force a paid chain using the invalid remaining artifacts.

## 2. What was implemented

### Guided Pi patch-only repair

The implementation is in:

- `images/pi-base/guided_patch.mjs`;
- `skillrace/pi_patcher.py`;
- `skillrace/patch_only.py`;
- `skillrace/patch_confirmation.py`;
- `skillrace/analyze_rq1.py`.

The patcher launches Pi 0.73.1 inside a fresh container. It mounts a writable copy of the
original skill and a read-only `repair-context.json`. SkillRACE evidence includes the
saved failure plus bounded reasoning episodes, thinking, tool calls/results, tree, guard,
and branch evidence when those fields exist. Baseline repairs receive only the common
failure evidence.

Pi has only `read`, `grep`, `edit`, and `write`. A reviewed SDK policy requires two
distinct direct reads—complete `SKILL.md` and complete repair context—before mutation.
Early `grep`, duplicate reads, paths outside the two inputs, edits outside `SKILL.md`, and
later mutations are blocked. Once both reads finish, only `edit` and `write` remain. Pi
has no shell, network tool, checker, replay image, package installer, or task-execution
tool. The prompt explicitly forbids rerunning or validating the failure while patching.

The runner hash and prompt version participate in the exactly-once operation identity.
The terminal patch result retains input/output/cache tokens, provider credits, turns,
tool-call count, mutation count, blocked-call count, unread-input count, and the final
event kind. Raw Pi sessions, event streams, and repair rationale are deleted. Structural
validation permits one changed file only: `SKILL.md`.

Patch generation and confirmation are separate operations. The patcher stops after the
edit. A later orchestrator launches one exact replay with the saved prompt, environment,
and patched skill. Only `repair_confirmed` contributes to confirmed-defect yield;
`same_failure`, `different_failure`, timeout, error, and inconclusive do not.

### Accounting and model support

Input, output, and cache-read tokens are stored separately. The Pi SDK reports uncached
input and cache reads as disjoint values; pricing reconstructs total input correctly
before applying the cache subset. Yunwu prices are custom provider credits (`⚡`), not
USD.

Checked-in Pi catalogs and structured-tool support exist for GLM-4.5-Flash, GLM-4.5,
GLM-4.7, Qwen3.5-Plus, both Qwen3 Coder candidates, DeepSeek-V4-Flash, DeepSeek-V3.2,
and the retained Grok development candidates. GLM-4.5-Air is directly callable but did
not reliably emit structured tool calls in the recorded probe. GPT-5.4-Mini uses the
Responses-style direct path and is not a stock-Pi agent model. The currently checked-in
headline protocol still names GLM-4.5-Flash and DeepSeek-V4-Flash; the user intends to
choose and hardcode the final two full-run models later. Development model availability
must not silently change a frozen headline track.

### Tests and documentation

The final focused command passed 76 tests covering Pi/direct patching, patch-only
receipts, exact confirmation, campaign repair protocol, repair evidence, exactly-once
behavior, and verified RQ1 analysis. `node --check images/pi-base/guided_patch.mjs` also
passed. The implementation design is synchronized in:

- `docs/superpowers/specs/2026-07-13-configurable-patch-only-repair-design.md`;
- `docs/pi-integration.md`;
- `docs/superpowers/plans/2026-07-14-guided-pi-repair.md`.

## 3. Paid live development evidence

These runs are prohibited from headline reuse.

| Run | Outcome | Input | Cache read | Output | Credits | Wall time |
|---|---|---:|---:|---:|---:|---:|
| Synthetic guided v2 | valid edit; post-run cost adapter crashed | 2,965 | 4,608 | 954 | 0.018008 | not retained as terminal |
| Synthetic guided v3 | valid completed edit | 3,462 | 4,480 | 1,269 | 0.019691 | 37.35 s |
| Genuine v1 | no mutation; inspection loop | 16,222 | 21,120 | 3,565 | 0.085379 | 98.36 s |
| Genuine v2 | no mutation; ten-turn stop | 21,983 | 50,560 | 4,057 | 0.157257 | 109.21 s |
| Genuine v3 | no mutation; dynamic switch never reached | 11,047 | 65,280 | 2,985 | 0.161609 | 94.44 s |
| Genuine v4 patch | completed one valid `SKILL.md` edit | 17,882 | 11,008 | 2,888 | 0.066444 | 86.52 s |
| Genuine v4 exact replay | `same_failure` | 49,160 | 515,712 | 16,421 | 1.179007 | 476.4 s agent time |

The first three genuine attempts motivated the deterministic two-read state machine. The
fourth then completed in five model turns with four tool calls, one blocked call, one
successful mutation, and zero unread required inputs. It added general guidance for
inconsistent IoT JSON, timestamp normalization, required fields, one-row-per-reading,
and conditional JSON serialization.

The replay remained `same_failure`. The strict verified cell contains two raw failure
observations, one unchanged-skill-reproduced cluster, zero confirmed events, and the
repair-validation status `reproduced-but-not-repaired` for that cluster. Its artifacts
are under:

```text
out/development-pilots/2026-07-14/guided-genuine-v4/
  patches/
  confirmations/
  analysis/cell.json
```

For future budgeting, 11 saved ordinary agent executions averaged about 21,200 uncached
input, 6,754 output, and 113,827 cache-read tokens, but they averaged only about four
minutes. Duration-normalized planning for a continuously active 15-minute run is roughly
72,000 input, 23,000 output, and 387,000 cache-read tokens. Use the conservative budget
75,000 input + 25,000 output + 400,000 cache-read tokens per 15-minute execution. A
timeout is a ceiling, not a token quota.

## 4. Confirmed failures and unresolved issues

### P0 — generated checker validity (offline gate completed)

The genuine v4 case is not a valid positive repair gate:

1. `valid-json-out` required JSON stdout although the task requested a clean pandas
   DataFrame and never required JSON output.
2. `parses-valid` ignored the actual `parse_sensor_data` function, selected `main`, and
   called it with an incompatible argument. The produced parser did create five rows,
   normalize timestamps, coerce values, and save a clean CSV.
3. The JSON checker contained a fallback that printed the checker input itself, which can
   confuse checker behavior with artifact behavior.
4. Several behavior checkers treat a missing required artifact as “vacuously holding,”
   allowing an agent that produced nothing to pass.

A read-only inventory of 284 generated development/scenario scripts found 21 scripts
matching the suspicious missing-artifact-vacuity pattern and 12 matching stdout-as-JSON
patterns. These are triage counts, not proof that every matched script is invalid.

The continuation implemented the recommended small design: one same-model pre-run
semantic self-audit call over the complete checker set for a candidate, followed by at
most one targeted rewrite per rejected checker. It rejects hidden requirements, guessed
callable signatures, invalid conditional preconditions, vacuous success for missing
required artifacts, and fallbacks that manufacture expected output. It remains pre-run
and shared byte-for-byte by all methods. This is explicitly a self-audit, not a claim of
independent or formal validation.

### Other known limitations

- The historical saved v4 method evidence had no reasoning episodes/tree/guard frontier,
  so it exercised the SkillRACE evidence path but did not demonstrate its trace advantage.
- The patch policy currently counts an accepted edit/write call as the one mutation before
  observing its tool result. A malformed edit tool call can therefore fail closed without
  a second edit attempt. Revisit only if real evidence shows this harms valid repairs.
- DeepSeek-V3.2 was usable for development but is not presently a frozen headline track.
- Yunwu route stability has varied by model and time. Preflight immediately before any
  bounded or headline launch remains necessary.
- The repository worktree contains extensive intentional uncommitted work from this long
  build. Nothing was reset or discarded. This handoff records filesystem state; it is not
  a Git release or clean-checkout proof.

## 5. Work remaining, in order

1. **Obtain one eligible saved failure.** Current-format saved failures were manually
   rechecked after the offline gate. The remaining non-json timeout starts from a
   validator that already solves its task; older smoke failures use obsolete contracts.
   Do not treat either as an eligible positive gate.
2. **Run a new valid bounded gate.** Select a failure whose prompt, property, invocation,
   and checker are manually defensible. Run exactly one fresh patch → independent replay
   → verified-cell chain. Do not rerun v4 or reuse terminal operation identities.
3. **Simplify the headline repair path.** Confirm/group before patching repeated raw
   failures, share patch-only/exact-confirmation code between RQ1 and RQ3, and remove the
   unused within-cell epoch mode before thousands of headline runs.
4. **Finish the bounded cross-method/model pilot.** Only after the valid gate, run the
   small Random + VeriGrey-inspired + SkillRACE pilot under the candidate final models.
   Inspect fairness, token/cost receipts, parallel scheduling, and method information
   boundaries. Do not tune prompts to a particular evaluated skill.
5. **Choose and hardcode the two headline models.** Re-run capability/rate/preflight
   evidence for the exact choices and rebuild/freeze the corresponding model catalogs and
   images. Each selected model is a complete independent repetition of all experiments.
6. **Promote draft identities.** Materialize the light protocol/suite/schedule/image/code
   freeze after implementation and pilots are complete. The user explicitly deprioritized
   elaborate freeze machinery; keep this proportional and do it before headline results.
7. **Run full experiments and analysis.** Execute 30 counted runs per method/skill/model
   for RQ1 and the matching three-producer RQ3 design, then all per-failure patch/replays,
   unchanged-skill confirmations, hidden evaluation, verified analysis, figures, and
   tables.
8. **Paper and artifact closure.** Replace result placeholders only from verified
   artifacts, reconcile paper/README claims, rehearse from a clean checkout, package the
   anonymized artifact, and prepare archival metadata/DOI material.

## 6. Safe restart point

Read this handoff, `STATUS.md`, and `docs/implementation-status.md`. Do not launch a paid
model or agent run first. Resume with the semantic checker-audit design. The guided Pi
patcher itself has crossed its mechanical implementation gate; the next uncertainty is
whether the upstream generated tests identify real failures.

At stop time there were no running Docker containers, guided patchers, confirmation
processes, or delayed cleanup shells.

## 7. Continuation update — minimal checker audit and simplification review

### Implemented

The approved minimal design is recorded in
`docs/superpowers/specs/2026-07-14-minimal-checker-semantic-audit-design.md`; its TDD plan
is `docs/superpowers/plans/2026-07-14-minimal-checker-semantic-audit.md`.

`skillrace/compile_checks.py` now:

1. authors and mechanically validates the existing per-property scripts;
2. fails before the agent if authoring remains mechanically invalid;
3. makes one fresh same-model semantic self-audit call over every script for the
   candidate;
4. validates an exact ordered JSON decision for every property;
5. rewrites each rejected checker at most once;
6. fails before the agent if that rewrite is mechanically invalid; and
7. records audit/rewrite/policy versions, decisions, original/final hashes,
   input/output/cache-read tokens, provider-credit costs, operation IDs, and redacted
   terminal receipt hashes in the existing manifest/fingerprint/accounting path.

The semantic rules cover all five P0 failures: unsupported requirements, guessed
artifact interfaces/signatures, missing conditional preconditions, missing-required-
artifact vacuity, and manufactured/echoed expected output. The same compiler path is
used by Random, VeriGrey-inspired, and SkillRACE. No separate auditor model, ledger,
service, or semantic framework was introduced.

### Offline evidence

The two exact saved json-parser failure patterns were reproduced in tests before the
implementation was added. The red-green cycles also cover malformed/incomplete audit
JSON, one-call batching, one-rewrite enforcement, fail-closed authored/rewrite scripts,
fingerprint version changes, cache behavior, token/cache/cost fields, and redacted
receipt identities.

Fresh commands and outcomes:

```text
.venv/bin/python -m pytest -q tests/test_checker_semantic_audit.py
  9 passed

.venv/bin/python -m pytest -q tests/test_checker_semantic_audit.py \
  tests/test_compile_identity.py tests/test_check_isolation.py \
  tests/test_campaign_engine.py tests/test_rq3_campaign_adapter.py
  73 passed

.venv/bin/python -m pytest -m 'not live'
  763 passed, 100 skipped in 36.12s

.venv/bin/python -m compileall -q skillrace tests
  exit 0

git diff --check
  exit 0
```

## 11. July 15 continuation — simplified post-run Python checker

The user rejected the accumulated pre-run Bash plus same-model semantic-audit design as
overengineered and unreliable. After discussing the information boundary, the approved
replacement was implemented test-first:

```text
sanity → agent → immutable final snapshot
       → path-only Python authoring → isolated checks → verdicts
```

The author receives the original task prompt, generated environment description, one NL
property, available tools, and final workspace paths only. It does not receive file
contents, trace/diff contents, stdout/results, verdicts, campaign feedback, or method
identity. A trace checker may read `/check/trace.jsonl` only when its frozen program
executes. The active path does not expose `/check/workspace.diff`.

Each standalone Python checker uses exit `0` for holds, `1` for violated, and `2` for
not considered. Local Python compilation is the only generation gate. A syntax failure
gets one targeted retry with the compiler error and old source; another failure excludes
only that property. There is no semantic-audit model call and no active generated Bash.
Timeout, staging failure, unavailable Python, unexpected exit, and checker-internal
failure are not agent violations. Every valid program runs in a fresh networkless child
of one immutable final snapshot.

`skillrace.loop` no longer calls `compile_case` before the agent. It passes blinded
metadata to `check_run` after a counted terminal agent execution. The new
`post-run-python-checks/1` manifest records prompt/policy versions, final snapshot and
path-tree identity, properties/applicability/tools/model, scripts and hashes, exclusions,
per-call operation and receipt identities, input/output/cache-read tokens,
provider-credit costs, and unknown-cost status. An outcome-unknown author call still
stops the campaign. Fixed checks and human-authored RQ3 hidden Bash checks retain their
existing paths.

The full offline test suite initially exposed nine failures with one cause: the old fair
scheduling test helper still monkeypatched the removed `skillrace.loop.compile_case`
symbol. The test harness was updated to assert the new sanity → agent → checker order;
all 11 scheduling tests then passed, followed by the complete no-live suite.

The property audit was rerun from the current manifests: 30 RQ1 skills/90 properties and
10 RQ3 scenarios/30 public properties still have nonempty unique IDs and supported
evidence kinds. No property text changed. The saved Bash inventory is now 308 scripts:
64 under `out/development-pilots`, 52 under `experiments/development-pilots`, and 192
human-authored RQ3 hidden checks. Manual inspection of the added July 14/15 diagnostics
again found missing-required-artifact vacuity, malformed-heredoc, and final-tree/diff
confusion in generated scripts; no such conclusion was transferred to the hidden suite.
At this implementation/audit point no generated post-run Python sample existed; Section
12 records the later explicitly authorized live validation.

The current design and implementation plan are:

- `docs/superpowers/specs/2026-07-15-post-run-path-only-python-checkers-design.md`;
- `docs/superpowers/plans/2026-07-15-post-run-path-only-python-checkers.md`.

This replacement supersedes Sections 7–10 only for the active checker architecture;
their paid-run accounting and failure records remain authoritative history. The paper
must describe the new oracle honestly as blinded post-run path-adaptive, not independent
pre-run. Before a live validation, rerun the artifact smoke, compileall, and diff gates.
Then use a fresh operation/campaign identity and manually inspect every generated
checker/verdict. Only after a defensible saved failure exists may the project run exactly
one fresh failure → patch → independent exact replay → verified RQ1 cell chain.

## 10. July 15 continuation — uncapped checker calls and real GLM/V3.2 behavior

The user chose the simplest experimental policy after reviewing the earlier 4,000-token
failures. The approved design and execution plan are:

- `docs/superpowers/specs/2026-07-15-unbounded-checker-generation-design.md`
  (commit `3b26f06`);
- `docs/superpowers/plans/2026-07-15-unbounded-checker-generation.md`
  (commit `7cd0728`).

The implemented `compile-check-v6` path omits the provider output-token field, uses a
120-second per-call timeout, asks for a concise script immediately, retries one
mechanically invalid checker exactly once with its error and previous output, and then
excludes only that property. The one batched semantic audit sees only mechanically
usable scripts. Semantic rejections are excluded without another rewrite call; a
candidate with no remaining properties is rejected pre-agent. The policy, exclusions,
tokens, cache reads, cost status, operation IDs, and receipt hashes are fingerprinted or
stored in the compile manifest.

`closeai.chat(..., max_tokens=None)` now omits both `max_tokens` and
`max_output_tokens` from provider payloads while retaining `max_tokens: null` in the
journal identity. Integer limits are unchanged. Development calls whose Yunwu tariff is
not frozen no longer crash cost aggregation: their receipt cost remains `null`, numeric
totals contain only known priced credits, and generator/compile artifacts record
`unknown-nonzero-possible`.

Two live findings were fixed test-first:

1. the random generator and checker result unpacker attempted `float(None)` for
   successful unpriced GLM-4.7 calls;
2. `bash -n` can return zero while warning about an unterminated heredoc. The warning
   is now mechanical invalidity, so it receives the existing one retry and then property
   exclusion. In addition, properties explicitly scoped to things introduced/changed by
   the agent or a modified test/file must inspect `/check/workspace.diff`. The full 90-
   property RQ1 scan found exactly three affected properties, all in
   `condition-based-waiting`; no RQ3 public property is affected.

### Fresh bounded development campaigns

Every campaign used a new output path and operation identities, budget one, at most two
pre-agent attempts, `epoch_size=1`, and a 600-second campaign clock. None is headline
evidence and none may be resumed.

1. `unbounded-checker-debugging-glm47-v1`: both proposal calls succeeded, then local
   unpriced-cost aggregation crashed. Terminal `aborted_pre_agent_attempt_cap`, zero
   agent starts. This directly motivated the accounting regression.
2. `unbounded-checker-debugging-glm47-v2`: accounting was fixed. Two candidates built,
   but sanity rejected one for a missing Go tool and the other for a required
   `package.json` it never created. Terminal `aborted_pre_agent_attempt_cap`, zero agent
   starts.
3. `unbounded-checker-finishing-deepseek32-v1`: both candidates were already solved
   according to their own sanity predicates. Terminal `aborted_pre_agent_attempt_cap`,
   zero agent starts.
4. `unbounded-checker-condition-glm47-v1`: both candidates reached one checker call,
   then the remaining `float(None)` compatibility conversion caused `compile_error`.
   Terminal `aborted_pre_agent_attempt_cap`, zero agent starts. This motivated the
   second accounting regression.
5. `unbounded-checker-condition-glm47-v2`: attempt 1 was sanity-rejected. Attempt 2
   authored five properties; `tests-not-weakened` failed both mechanical attempts and
   was excluded, the audit accepted four, and the GLM agent completed in 67.4 seconds.
   The campaign initially reported `bounded-fresh-polling` and
   `timing-waits-justified`. Manual review invalidated both as defect evidence: the first
   checker used `EOF "$TEST_FILE"` as a heredoc terminator, producing embedded Python
   `SyntaxError`; the second scanned the complete final test and blamed the agent for the
   legitimate 100 ms event-trigger delay present in the initial file. Do not patch or
   replay this failure. Terminal campaign status `completed`, one agent start.
6. `unbounded-checker-condition-deepseek32-v1`: attempt 1 exhausted the 300-second
   realization deadline. Attempt 2 authored/retried its checkers, but the one batched
   audit rejected every mechanically usable script; compilation correctly stopped with
   `no usable property checkers after semantic audit`. Terminal
   `aborted_pre_agent_attempt_cap`, zero agent starts.

The GLM compile manifest records six author calls totaling 9,782 input, 1,954 output,
and 2,944 cache-read tokens plus one audit call of 3,264 input/223 output. All seven
calls succeeded with omitted output limits and unknown provider-credit price. The
DeepSeek viable attempt records eight author/retry calls totaling 8,895 input, 4,044
output, 4,096 cache-read tokens, and 0.029922 credits, plus one audit call of 3,612
input/266 output and 0.008022 credits. No checker call was truncated at a configured
output ceiling.

Across all journal operations from 02:18 UTC through the final pilot there are 43
successful direct-call terminals: 58,350 input, 16,616 output, 17,024 cache-read tokens,
and 0.070785 known provider credits. The 25 GLM-4.7 terminals account for 35,847 input,
8,023 output, and 10,112 cache reads with unknown cost; the 18 DeepSeek-V3.2 terminals
account for 22,503 input, 8,593 output, 6,912 cache reads, and all 0.070785 known
credits. The completed Pi agent additionally has a successful external-usage receipt,
operation `966c43a1e0f647aba794b3161407e341`: 8,126 input, 1,416 output, 20,608 cache-read
tokens, nine turns, and unknown cost. Its runner terminal is `completed`, return code 0.
There are no outcome-unknown calls in this continuation window.

### Stopping decision

The minimal checker path now runs end to end with both requested models. The GLM run
also demonstrated why a generated script's nonzero exit cannot automatically be trusted;
the two general pre-run validations above now cover the observed patterns and invalidate
the saved development verdict. DeepSeek correctly failed closed when its scripts were
not semantically defensible. Therefore there is still no different, manually defensible
saved failure for the required failure → patch → independent exact replay → verified
RQ1 chain. No patch call was made and no invalid failure was forced through the gate.

At stop time no agent, generator, checker, patcher, replay, or delayed cleanup process is
running. Do not reuse any operation or campaign named in this handoff. The worktree still
contains extensive intentional uncommitted work and must not be reset or cleaned.

## 9. July 15 real Yunwu validation

At the user's request, one fresh bounded campaign exercised the simplified path with
real Yunwu calls:

```text
skill: condition-based-waiting
method/model: SkillRACE / deepseek-v4-flash
budget/epoch: 1 / 1
output: out/development-pilots/2026-07-15/
        checker-audit-condition-waiting-dsv4-v1
```

Attempt 1 passed proposal, realization, build, and sanity. Its five checker-authoring
calls demonstrated the actual token behavior:

- 2,391 output tokens: usable script;
- 3,387 output tokens: usable script;
- 4,000 output tokens: empty script;
- 4,000 output tokens: syntactically truncated script; and
- 4,000 output tokens: empty script.

Thus the larger ceiling improved two calls but did not make DeepSeek checker generation
reliable. The attempt failed mechanically before semantic audit or agent execution.
Attempt 2 proposed and realized a Python candidate, then its generated Dockerfile stalled
in unnecessary `apt-get update`/`pip install` work. The local build timed out as the
campaign was interrupted. A `generate.build-repair` call had just published intent
`a20680c7de4f4b4aaf34da450f2357d5`; it has no terminal receipt and must be treated as
outcome/cost indeterminate. The campaign remains diagnostic `running`, has zero agent
starts, and must never be resumed.

The July 15 ledger totals are 9 known-success terminals, 12,626 input tokens, 23,417
output tokens, 5,376 cache-read tokens, and 0.05419152 provider credits, plus the one
unmatched build-repair intent. No prior identity was reused.

One further simplification was implemented test-first from this evidence: checker
compilation now stops at the first mechanically invalid script instead of paying to
author the remaining properties of a candidate that cannot proceed. The focused checker
suite passed 39 tests. No additional paid call followed this change.

No model, agent, patcher, replay, or other paid call occurred during implementation or
audit.

### Full suite audit

`docs/2026-07-14-checker-suite-audit.md` records the offline review of all 30 RQ1 skills
(90 properties), all 10 RQ3 public suites (30 properties), and 284 saved check scripts.
No property specification changed. A broader fresh triage found 24 nearby
missing/empty-artifact→`exit 0` matches, 16 stdout/output-as-JSON matches, two guessed
callable lists, and one manufactured-output fallback. Manual review confirmed 22 invalid
missing-required-artifact paths and both original P0 bugs. These broader counts differ
from the handoff's 21/12 regex counts and are not defect totals. None of the suspicious
missing-artifact-vacuity matches came from the 192 human-authored RQ3 hidden checks.

### Whole-pipeline simplification review

`docs/2026-07-14-pipeline-simplification-review.md` traces the actual experiment path and
classifies its gates. The highest-value pre-headline simplifications are:

1. group and unchanged-confirm failures before paying to patch/replay one representative;
2. use the patch-only plus exact-confirmation implementation in both RQ1 and RQ3;
3. delete the unused within-cell `epoch_size > 1` production branch while retaining
   top-level cell parallelism; and
4. use one lightweight final freeze manifest.

Those broader changes were deliberately not mixed into the P0 checker patch. The next
bounded gate should exercise the current small core before more refactoring.

### Saved-failure selection failure

The current-format saved-failure inventory was manually rechecked before spending money.
All completed property failures other than the known json-parser cases were exhausted.
The only non-json case is
`positive-gate-v2/network-config-validation/.../cand-ab2f5ee73998`, which timed out on the
fixed termination property. Its generated initial `/workspace/validate.py` already
detects both prompt-required findings, and the sanity “unsolved” predicate incorrectly
accepts the presence of those findings as evidence that the task is unsolved. It is not a
valid skill-failure gate. Older `out/campaign-smoke` failures use obsolete campaigns and
checkers; one representative also uses `collections.py`, which shadows Python's standard
library and prevents both pytest and its Python checker from starting.

Therefore no eligible saved failure exists under the user's required validity rules. No
paid call was made, no prior terminal operation identity was reused, and no invalid case
was forced merely to complete a positive-looking chain.

## 8. Continuation update — bounded Yunwu diagnostics and simpler checker authoring

The user then authorized bounded Yunwu calls to exercise the full path. No headline
protocol was run. Two fresh connectivity preflights succeeded:

- `deepseek-v4-flash`: 10 input, 32 output, 0 cache-read tokens, 0.000074 credits;
- `glm-4.5-flash`: 13 input, 231 output, 190 cache-read tokens, 0.00001874 credits.

Four manifest-ordered, development-only, budget-one SkillRACE campaigns used fixed
protocols, `epoch_size=1`, fresh output directories, and at most two pre-agent attempts:

1. `fastapi-endpoint` / DeepSeek: terminal
   `aborted_pre_agent_attempt_cap`; both realizations were unparsable; zero agent starts.
2. `cli-argparse-fix` / GLM: candidate realization/build/sanity succeeded, but checker
   operation `60c9fc625cf9425fbd7b0c9e5058755e` reached the 180-second whole-call deadline.
   Its durable terminal status is `outcome_unknown`, with unknown tokens and cost. The
   executor incorrectly classified it as retryable `compile_error`, so the campaign
   began operation `92de1a7e48ec4a1d90797e3804cccadd`. It was immediately interrupted;
   the ledger contains an unmatched intent, so its outcome/cost are also indeterminate.
   The campaign remains diagnostic `running` state and must never be resumed.
3. `cli-subcommand-validator` / DeepSeek: terminal
   `aborted_pre_agent_attempt_cap`; attempt 1 failed checker mechanics after an empty
   correction response (122.446 seconds), and attempt 2 was correctly sanity-rejected
   because the candidate already solved the task (6.446 seconds); zero agent starts.
4. `code-refactor-fowler` / DeepSeek: terminal
   `aborted_pre_agent_attempt_cap`; both attempts failed checker mechanics after
   108.647 and 99.733 seconds; zero agent starts.

Across all calls at or after 13:00 UTC, the permanent ledger has 31 known-success
terminals totaling 29,722 input tokens, 47,914 output tokens, 11,520 cache-read tokens,
and 0.10632236 Yunwu provider credits. By tag: authoring used 15 calls/13,035 input/
22,758 output/0.05052284 credits; proposal used 7/6,625/5,181/0.01273456; realization
used 7/10,039/19,712/0.04297222; preflights used 2/23/263/0.00009274. Add the terminal
unknown GLM checker call and unmatched interrupted GLM generation intent; neither has
defensible token or cost totals. No July 14 identity was retried or resumed after audit.

### Outcome-unknown regression fix

Root-cause tracing showed that `_DirectExecutor.execute` caught every compile exception,
including `OutcomeUnknownError`, and returned ordinary `compile_error`; candidate
generation likewise collapsed the exception to ordinary `generation-error`. Two tests
were written red first. The minimal fix classifies either boundary as
`external-outcome-indeterminate`, records `unknown-nonzero-possible`, and durably ends
the campaign as `aborted_external_outcome_unknown` without counting an agent run or
proposing another candidate. Ordinary known compile failures still use the existing
bounded retry rule. The focused campaign regression set passed 54 tests before the paid
campaigns continued.

### Evidence-based simplification

The DeepSeek checker responses that failed mechanics consistently ended at exactly the
old 1,600-token ceiling and produced one-byte scripts; the paid mechanical correction
often did the same. This occurred before the semantic audit and accounted for most
pre-agent calls. The simple model-wide change, implemented test-first, is:

- `compile-check-v4` uses a 4,000-token ceiling for author, audit, and semantic rewrite;
- there is one initial authoring call per property;
- a mechanically invalid initial script rejects the candidate immediately, with no paid
  mechanical-correction call; and
- only a semantic-audit rejection can receive the already-bounded single rewrite.

The prompt content was not tuned for a skill or method. The ceiling and flow are included
in the compile fingerprint and apply identically to all three methods and both tracks.
No additional paid call was made after this change. The requested valid
failure → patch → independent exact replay → verified RQ1 chain remains incomplete
because every fresh campaign produced zero agent starts.

Fresh final verification after the simplification:

```text
.venv/bin/python -m pytest -q -m 'not live'
  exit 0

PYTHON=.venv/bin/python scripts/artifact_smoke.sh
  SkillRACE offline artifact smoke: PASS

.venv/bin/python -m compileall -q skillrace tests
  exit 0

git diff --check
  exit 0
```

## 12. July 15 live validation of post-run Python checks

After the offline gate passed, the user explicitly authorized fresh Yunwu validation.
No old campaign or operation identity was resumed.

### GLM-4.7 / validator-agent / Random

`post-run-python-glm47-validator-v1` completed one counted agent execution and authored
four syntactically valid v1 Python checks. The campaign reported only
`valid-inputs-accepted` violated. Manual review rejected that finding: the checker
invented newline-separated input (`1\n0\n`) although the validator's documented/source
interface requires a space after `N`. This is a checker-interface defect, not an agent or
skill defect, so no patch/replay was launched.

Accounting: proposal 834 input/98 output, realization 1,247/485, agent 8,579 input/2,111
output/18,432 cache-read, and checker authoring 1,663 input/3,319 output/0 cache-read.
Agent time was 45.6 s; complete execution time was 155.009 s. GLM-4.7 has no frozen
tariff, so provider-credit cost is unknown.

### DeepSeek-V3.2 / log-parser / Random

`post-run-python-deepseek32-log-parser-v1` completed one counted agent execution and
authored four syntactically valid v1 Python checks. All four reported violations were
manually invalidated:

1. `log-filters-correct` treated every 16:xx timestamp as within a window ending at
   exactly 16:00, producing an expected count of five instead of four.
2. `malformed-lines-policy` invented an exact `timestamp,level,message` header although
   the task/skill preserves valid record fields.
3. `csv-output-faithful` made the same unsupported three-column/order requirement.
4. `parser-reruns-without-source-mutation` invoked positional input/output arguments even
   though the documented CLI uses required `-i/-o` flags.

Accounting: proposal 887/102/0 tokens and 0.002080 credits; realization 1,331/797/0 and
0.005053; agent receipt 8,694 input/4,564 output/78,720 cache-read and 0.188520; checker
authoring 1,985 input/3,085 output/0 cache-read and 0.013225. Agent time was 170.7 s;
complete execution time was 280.726 s. Total known campaign cost was 0.208878 credits.

### Generic v2 prompt correction and bounded follow-up

The cross-model root cause is that Python fixes syntax and paths but does not stop the
author from guessing an underspecified interface. A red test was added, then the fixed
prompt was versioned to `post-run-python-check-v2` /
`path-only-three-state-no-guess-v2`. It tells the generated runtime program to inspect
documentation, source, or `--help`; forbids invented callable/CLI/input/header/bound/
expected-value assumptions; and requires exit 2 when the expectation remains
underdetermined. No semantic-audit service or extra normal call was added. The focused
114-test checker/campaign/RQ3 boundary passed.

One fresh v2 DeepSeek `csv-workbench` campaign used the two permitted pre-agent attempts.
Attempt 1 built but was sanity-rejected (`task-probe`, 14.441 s). Attempt 2's realization
ended at exactly 4,000 output tokens and was unparsable. The campaign terminated
`aborted_pre_agent_attempt_cap` with zero agent/checker calls. Its four generation calls
totaled 3,147 input, 4,929 output, zero recorded cache reads, and 0.021081 credits.

A final checker-only invocation from Python stdin was an invalid diagnostic because the
journal client's spawn process cannot import `<stdin>`. Operation
`4eeeacd2a8694d17b8a1e142fb27e5f0` produced three immediate local `ProviderError`
terminals with no HTTP status, usage, or recorded cost. The property was excluded and
the identity must not be retried. This is not live evidence for or against v2.

No reported violation from these campaigns is defensible, so the required single
failure → patch → independent exact replay → verified RQ1 cell chain remains blocked at
failure selection. No patch call was made. At stop time no campaign, agent, checker,
patcher, replay, or Docker run container remained active.

## 13. Final July 15 pricing note and shutdown state

After the GLM-4.7 validation run, the user supplied this development tariff:

| Input tier | Output tier | Input price / 1M | Output price / 1M |
|---|---|---:|---:|
| 0–32,000 | 0–200 | 2,000 credits | 8,000 credits |
| 0–32,000 | 201–200,000 | 3,000 credits | 14,000 credits |
| 32,001–200,000 | unlimited | 4,000 credits | 16,000 credits |

The user clarified that cache-read tokens should use the applicable input rate. The
proposed interpretation is to choose the tier for each individual provider request and
then sum the requests, rather than choosing one tier from campaign-wide aggregate
tokens. Applying that interpretation to the retained journal gives a retrospective
estimate of 90.711 credits for the GLM agent and 0.064438 for proposal, realization,
and four checker-author calls, or 90.775438 credits total. These are estimates, not
receipt-recorded costs: the rate card, tier calculation, and cache treatment have not
been implemented, approved as a frozen experiment tariff, or tested. No pricing code
was changed after the user supplied the table.

The session stopped without launching another paid call or a patch/replay chain. The
final audit found no running SkillRACE campaign, agent, generator, checker, patcher,
confirmation/replay, delayed cleanup process, or Docker container. It also found no
unmatched recent operation intent. Do not resume any campaign or reuse any operation
identity recorded above. Preserve the extensive intentional uncommitted worktree.
