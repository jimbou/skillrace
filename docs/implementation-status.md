<a href="../README.md"><img src="../skillrace-icon.png" alt="SkillRACE" width="54" align="right"></a>

# SkillRACE implementation status

**Status date:** 2026-07-15

**Measurement status:** no headline RQ1 or RQ3 experiment has been run.

**Protocol status:** the main protocol and dataset manifest are still `draft`.

This page answers two questions: what has actually been built and verified, and what is
still required before the ISSTA measurements can begin. For the complete experimental
contract—including what counts as a run, fairness controls, failure/defect calculations,
RQ3 grading, cost accounting, parallelism, and validity limitations—read
[evaluation-reviewer-guide.md](evaluation-reviewer-guide.md). The approved detailed
design is [the evaluation specification](superpowers/specs/2026-07-11-skillrace-evaluation-design.md).
The exact stopping record for the latest implementation and paid diagnostics is the
[July 14 session handoff](2026-07-14-session-handoff.md).

Historical files under `docs/superpowers/plans/` record how implementation work was
organized. They are not the current experiment protocol; where they mention extra
ablations, six RQ3 conditions, three hidden repeats, a per-skill Greybox sweep, or shared
seeds, the lean evaluation guide and specification supersede them.

## 1. Current bottom line

The campaign engine, three testing methods, isolated property checker, per-failure
repair, receipt-backed RQ1 analysis, public datasets, RQ3 revision/evaluation pipeline,
durable model journal, and deterministic parallel execution machinery are implemented.
`aggregate.py` keeps a clearly labelled legacy diagnostic path, while verified headline
analysis requires campaign, confirmation, and repair receipts.

The P0 checker path has been simplified. RQ1 now runs the agent first, then authors one
standalone Python checker per property from blinded task metadata and final workspace
paths only. There is no semantic-audit call, no active generated Bash, and no workspace
diff in the new checker interface. Python compilation permits one syntax-guided retry;
another failure excludes only that property. Every valid check runs in a fresh child of
one immutable final snapshot. The earlier GLM-4.7 and DeepSeek-V3.2 Bash/audit pilots are
historical diagnostics; their only started-agent result was manually invalidated and is
not an eligible saved failure.
Fresh v1 validation then completed one GLM-4.7 validator run and one DeepSeek-V3.2
log-parser run. Manual review invalidated all five reported Python-checker violations
because they guessed input whitespace, a time boundary, CSV columns/order, or CLI
syntax. The generic v2 prompt now forbids such guesses, requires runtime inspection of
documentation/source/help, and uses exit 2 when the exact expectation is
underdetermined. A bounded v2 CSV campaign never reached an agent (one sanity rejection,
one invalid realization), so v2 still needs one valid live sample.
The remaining closure work is:

1. obtain one current-format, manually defensible saved failure and run one new bounded
   patch/replay gate; the remaining saved non-json timeout is not eligible because its
   generated starting validator already solves the task;
2. simplify the headline repair order to confirm/group before patching and share the
   patch-only implementation between RQ1 and RQ3;
3. obtain a successful direct and Pi preflight from both finally selected Yunwu routes;
4. complete the bounded five-cell dual-model paid pilot and inspect its raw receipts
   without tuning to an evaluated skill;
5. promote the now-verified 30-skill D1 and 100-test D2 runtime identities into the
   intentionally light final freeze;
6. promote the already complete one-replication schedules only after their input gates
   pass; and
7. only then run the paid headline campaigns.

## 2. Experiment that the implementation supports

| Study | Conditions | Allocation | Fixed agent executions |
|---|---|---|---:|
| RQ1: public skill testing | Random, VeriGrey-inspired L1, SkillRACE on 30 skills, repeated under two models | 30 per method/skill/track | 5,400 |
| RQ2: mechanism | Labels from the same SkillRACE RQ1 executions | no extra arm | 0 extra |
| RQ3 public testing | the same three producers on 10 base skills, repeated under two models | 30 per producer/scenario/track | 1,800 |
| RQ3 hidden final exam | zero-shot plus three feedback revisions on 10×10 tests, per model | each test once per track | 800 |

The lean design has one predeclared campaign replication. Random spends all 30 counted
executions on fresh independent cases. VeriGrey-inspired and SkillRACE each spend ten on
independently generated bootstrap cases and 20 on guided exploration. The intended fixed
total is 8,000 agent executions across two separately reported tracks. Every failed
RQ1/RQ3 public search execution additionally receives one independent patch and one
replay, adding between zero and 7,200 agent executions; distinct suspected groups still
receive one separate unchanged-skill confirmation. The absolute fixed-plus-repair bound
is 15,200 plus confirmations. Base
generation, internal semantic processing, check compilation, patching, and aggregate
revision are model calls whose cost is recorded but which do not masquerade as agent
executions.

## 3. What is implemented

### 3.1 Fair campaign protocol and three methods

- `experiments/protocols/issta-main.glm-4.5-flash.draft.json` and
  `issta-main.deepseek-v4-flash.draft.json` independently fix their model, budget 30,
  bootstrap count 10, five pre-agent attempts per coordinate, one global L1 Greybox
  schema, and the shared generator configuration. The dual manifest forbids pooling.
- The protocol authoritatively maps allocations to Random `0+30`, VeriGrey-inspired
  `10+20`, and SkillRACE `10+20`; the production path cannot silently turn Random into a
  seeded method or select a different Greybox level per skill.
- Random receives no execution feedback. VeriGrey-inspired sees only schematized L1 tool
  events and novelty over tools, transitions, and full sequences. SkillRACE sees its own
  episodes, outcomes, tree, guards, and properties for target selection.
- All three use the same candidate realization/build/repair path, sanity gate, Pi runner,
  base image per skill, wall-clock policy, property specs, compiler/checker, model, and
  provider journal policy.
- The comparison is explicitly full-system. No implemented or planned headline claim
  says that Random→Greybox isolates feedback or that Greybox→SkillRACE isolates a single
  component.

### 3.2 Exact budget and crash accounting

- A coordinate consumes budget only after Pi is durably recorded as started.
- Proposal, schema, build, or sanity failures before that marker do not consume one of
  the 30 slots, but their attempts, cost, time, and reason remain reportable.
- Completion, agent error, timeout, lost outcome, or oracle-inconclusive status after the
  marker consumes the slot. A method cannot erase a bad started execution.
- Execution and attempt coordinates are transactionally reserved and unique. Start,
  terminal, cleanup, fold, and generator-state receipts are immutable and checked on
  resume.
- If an external action may have happened but lacks a terminal receipt, its state is
  `unknown`; the system stops rather than silently repeating a paid call.
- The outer scheduler marks a cell successful only when it returns both `complete: true`
  and terminal status `completed` with all 30 distinct counted coordinates.

### 3.3 Candidate validity and property oracle

- Every candidate passes the same non-semantic schema, path, build, base-integrity,
  workspace/tool, task, and obvious-start-state sanity checks before the agent can run.
- Natural-language RQ1 properties are authored after the agent into standalone Python
  programs. The author sees the task prompt, environment description, property, tools,
  and final workspace paths, but no file contents, trace/diff contents, result, verdict,
  or method identity.
- This is a blinded post-run path-adaptive generated oracle, not an independent pre-run
  oracle. The exact same authoring and execution path is shared by Random,
  VeriGrey-inspired, and SkillRACE.
- Python syntax is checked locally. One syntax failure receives one targeted retry with
  the compiler error and old source; a second failure or ordinary author failure excludes
  only that property as not considered. There is no semantic-audit LLM call.
- Exit `0`, `1`, and `2` map to holds, violated, and not considered. Missing
  task-required artifacts are violations; absent genuine conditional preconditions hold.
  Checker failure, timeout, unavailable Python, and unexpected exits are not agent
  violations.
- The post-run fingerprint/manifest binds model/prompt/policy, properties,
  applicability, available tools, final path tree and snapshot identity, script hashes,
  exclusions, input/output/cache-read usage, provider-credit cost, and redacted journal
  receipt identities. Authoring has no output-token ceiling and a 120-second timeout.
- The final state is snapshotted once. Each compiled check executes in a fresh,
  networkless, capability-dropped, process- and timeout-bounded child, so one check cannot
  prepare state for another.
- Exact trace checks structurally parse tool-call blocks rather than searching narration.
  Invalid scripts, missing evidence, Docker failure, and timeout yield inconclusive—not a
  fabricated pass or defect.

### 3.4 SkillRACE exploration and RQ2 evidence

- Concrete traces are segmented into episodes and summarized using tool outputs for
  outcomes. Episodes fold into a behavior tree; reasoning/outcome conditions generate
  candidate mutations.
- Selection is property-first and branch-diverse. Mutation may coherently change several
  environment features and may discover a useful branch other than the motivating one.
- Intended reach is diagnostic only. Implemented labels are `intended_branch`,
  `different_new_branch`, `no_divergence`, `path_miss`, and `unfolded`.
- Targeted versus serendipitous is a separate property relationship: a finding is
  targeted when its violated property ID equals the mutation's selected target property.
  Neither label gates whether a confirmed defect counts.

### 3.5 Defect grouping and confirmation primitive

- A definite `holds: false` property verdict is a suspected failure, not immediately a
  unique defect.
- Mechanical normalization removes volatile addresses, paths, and numeric literals from
  the detail, then hashes property ID plus normalized detail. Groups use
  `(skill, property, failure_signature)`.
- The earliest representative of each group is rerun once after search. Only the same
  property/signature recurring with status `confirmed` becomes a reproduced finding;
  timeout, error, inconclusive, or not-reproduced remains separate. A reproduced group
  enters headline repair-validated yield only when the representative's already-required
  independent patch makes the exact case pass every originally failed property.
- Confirmation consumes time and money but not one of the 30 search slots. Its full cost
  is recorded. The same confirmation machinery is integrated in both RQ1 and RQ3 and
  feeds the receipt-verified headline analyzer.

### 3.6 Deterministic parallel execution

- Independent method/skill/replication cells can run concurrently through one manifest
  scheduler and global resource pool with separate API, Docker, and agent limits.
- Frozen headline cells use `epoch_size=1`: all three methods preserve exact per-counted-
  execution retry coordinates, and adaptive methods fold a result before choosing the
  next case. Six independent RQ1 cells feed one API/Docker/agent pool capped at 4/3/3.
- The parallel-epoch engine and its transactional batch reservations remain tested for
  development use, including reverse-completion determinism, but a frozen protocol now
  rejects within-cell epochs greater than one. This closes a pilot-discovered case where
  pre-agent failures could skip `eNNNN-aNN` retry coordinates.
- Partial generation intent and each sequential fold can be recovered without
  double-spending a coordinate.
- Hidden tests and checks are currently isolated but sequential inside a scenario. RQ3
  scenarios are independent; complete draft top-level schedules are checked in for both
  tracks with two preparation workers and three independent scenario workers. Their frozen copies are
  intentionally withheld until the image, D2, pilot, and recursive freeze gates pass.

### 3.7 D1 public skill suite

- The draft headline set contains 30 redistributable public skills, 90 properties, and
  20 families: 26 high- and four medium-environment-contingency skills.
- The historical 22-skill balanced pre-result boundary is preserved. Surviving records
  do not prove it was a literal prefix of the S5 popularity array. The pre-result July 12
  continuation instead freezes that 628-row pool, walks stored popularity order,
  partitions every row through index 445, and stops at eight additional strict admits.
- Four development-used skills are excluded from headline inference regardless of
  whether their source ancestry is local or public.
  Three mined public candidates are excluded because redistribution permission is absent
  or unsafe.
- Thirty-three source records pin repository, commit, path, source hash, fidelity, and
  license evidence; 25 upstream license texts are embedded. Selection, source,
  licensing, construction-image, dual-overlay, and fresh networkless runtime audits pass
  for all 30 skills and both models. The remaining D1 step is identity promotion into
  the frozen suite and lock copies.
- Contingency was classified before results and is never a post-result inclusion rule.

### 3.8 D2 skill-generation suite

- Ten scenarios contain exactly ten hidden tests each: 100 tests and 192 executable
  criteria in total.
- The current matrix contains **100 validated Docker evidence records**, one for every
  hidden test contract, and all 192 checks run inside the built containers.
- Each test has a versioned public/hidden contract, Dockerfile and check hashes, a
  reference overlay, and assigned negative implementations.
- The earlier evidence was explicitly reset to `pending-docker` before moving hidden-test
  templates to the Pi 0.73.1 construction base. The replacement fresh-container matrix
  now records 100/100 references passing, 100/100 empty starting states rejected, and all
  215 assigned negative/criterion pairs killed. Root validation reports
  `pending_docker=0`, `audit_failed=0`, and `runtime_ready=true`.
- The negative implementations validate the oracles only. They are never counted as bugs
  found by any testing method.
- Hidden-test templates pin the shared Pi 0.73.1 construction base. The condition-blind
  executor projects only that base reference to the selected GLM/DeepSeek Skillgen
  overlay and records source/projected hashes plus immutable runtime image IDs.

### 3.9 RQ3 public phase, revision, and hidden exam

- The outer driver prepares a private scenario copy per model track, makes one
  exactly-once `/2` zero-shot generation call per scenario/model, and binds the result
  while requiring the normalized benchmark-template hash to remain cross-track equal.
- A scenario stages only its public purpose/campaign package and the prepared track's
  journal-provenance `/2` zero-shot base skill.
- The same base skill receives three 30-execution campaigns, separate deterministic
  failure grouping/confirmation, and three feedback envelopes.
- Every envelope has the same schema and a maximum of 3,600 canonical-JSON UTF-8 bytes.
  Section round-robin preserves findings, explored situations, and method-specific
  evidence fairly under truncation.
- The three revision calls use the same model, prompts, reasoning/temperature/output
  settings, and base skill; only the envelope changes. Each `/2` artifact binds exact
  request bytes, stable operation identity, hashed provider identity, usage, cost, and
  copied immutable journal receipts.
- Hidden evaluation is exactly four conditions × ten tests. A functional pass requires
  exactly the unique criterion IDs from the current hidden contract, all with
  hidden-independent provenance and all holding. Missing, extra, duplicate, or wrong-
  provenance criteria cannot pass.
- The headline denominator remains ten scheduled tests. Error, timeout, missing, or
  inconclusive execution yields no pass and is separately reported. Strict pass also
  requires applicable fixed invariants.
- Recursive verification reloads the current hidden contracts, resolved validation image
  digest, and exact `t1..t10` inventory; rehashes raw launch/run/trace/verdict/cost files;
  and recomputes grades rather than trusting stored summaries.
- RQ3 analysis treats scenarios as the top-level paired units and reports each revision's
  pass-rate change from its same zero-shot skill. It does not treat 100 tests as 100
  independent scenarios.

### 3.10 Hidden-information isolation

- Production campaign processes use an empty-root bubblewrap namespace. Explicit
  runtime/code/public inputs are read-only; only the campaign output and provider ledger
  are writable; the source scenario and `tests/` tree are absent.
- Confirmation reruns and revision run in separate confined children with only their
  required public inputs. A completed, hash-verified public-phase barrier precedes any
  hidden-test content loading or execution.
- Launch artifacts record the exact bubblewrap binary/hash/version, argv, mounts and
  modes, clean environment-variable names, and policy hash; resume recomputes them.
- Trusted orchestration retains host network access and the exact Docker Unix socket.
  This is an explicit trust boundary: it could ask Docker to bind a host path, although
  generated agents never receive the socket and production commands use recorded public
  mounts. The artifact does not claim hostile-container security.

### 3.11 Yunwu operation journal

- Calls use stable operation IDs and exact request hashes with atomic/fsynced starts,
  attempts, terminals, and call-terminal receipts.
- Provider identifiers are hashed; secrets and raw provider IDs are not stored.
- Missing usage/cost is represented as unknown rather than false zero. Production pricing
  fails closed for an unknown model.
- Strict `/2` base/revision validators reject missing or inconsistent model, request,
  usage, cost, billing, provider, operation, or journal provenance.
- `scripts/yunwu_hello.py` is the canonical one-call diagnostic; the legacy-named
  `closeai_hello.py` compatibility wrapper uses GLM and the same durable journal. Neither
  is an experiment.

## 4. Verification evidence available now

The following are targeted engineering checks, not scientific outcomes:

- campaign accounting, recovery, information-boundary, frozen-epoch, and reverse-order
  tests have passed in focused suites;
- the Pi 0.73.1-bound D2 replacement matrix passes all 100 tests and 215 assigned
  negative/criterion pairs;
- the journal/base/revision provenance suite passed 77 focused tests after the `/2`
  migration;
- the strict hidden-evidence RQ3 surface passed its focused 83-test suite;
- public-phase/confirmation/revision isolation passed 12 isolation-specific tests and a
  100-test broad RQ3-focused selection, including real bubblewrap and Docker probes.

After the RQ3 integrations and this documentation update were combined, the complete
repository suite reported **562 passed and 100 skipped** on 2026-07-12. Full Python
compilation and `git diff --check` also exited successfully. These are implementation
consistency checks, not scientific outcomes, and still need a clean-checkout artifact
rehearsal before freeze.

On July 13, after the final-state checker regression, the complete no-live suite again
ran to 100%, and `scripts/artifact_smoke.sh` passed with required D1 images. The latter
also validated the two model rate/probe archive and the D2 runtime matrix (10 scenarios,
100 tests, 192 checks). A live two-execution DeepSeek/SkillRACE component smoke had
already exercised candidate realization, Pi, trace folding, fallback selection, repair,
and the development-only zero-confirmation ledger. Its agent runs completed, found no
defect, and must not be used as scientific evidence; the generated state-property
verdicts were inconclusive because checker children inherited the agent container's
`sleep` entrypoint. The checker now overrides that entrypoint, and a Docker regression
test reproduces the old condition and passes. A fresh repeat was then blocked before any
agent start by Yunwu HTTP 429 responses for DeepSeek's upstream group, including a bare
provider-minimal request. This is a provider preflight failure, not a method result.

The first GLM-only three-method development launch then found a generic false rejection:
the candidate policy rejected a workspace script's ordinary `/usr/bin/env` shebang merely
because the interpreter path is protected. The path-reference ban was narrowed and a
failing-then-passing regression now preserves the shared post-build runtime fingerprint as
the actual authority for executable integrity. No agent started in that launch. Two later
GLM realizer calls stayed open past their requested timeout, so it was deliberately
terminated and its unresolved journal operations are non-resumable development evidence.
`deepseek-v3.2` has passed direct structured-realization, a short Pi skill/task probe,
and a direct call through the production wall-clock transport. It is a development
candidate, not currently a selected track: its user-reported provisional development rate
is recorded separately, but it remains outside the frozen model/rate/image catalog until
the final model inventory is hardcoded. It can enter headline work only if its archival
rate evidence, Pi/D1 images, direct/Pi preflights, protocols, schedules, and analysis
inputs are added before freeze; otherwise it remains development-only.

A later GLM/V3.2 development sequence reached a complete V3.2 Pi execution with native
reasoning but intentionally stopped before producing a pilot claim. It found and fixed
generic realization-repair preservation, sanity-contract clarity, and capability-dropped
checker staging defects. Once scripts executed, one generated oracle reversed its own
exit-code meaning, proving that unchanged-skill reproduction alone cannot validate a
model-authored oracle. The strict RQ1 analyzer now retains reproduced groups separately
and admits only groups whose representative exact case also becomes a pass after the
already-required independent original-skill patch. This makes the headline explicitly
repair-validated end-to-end yield; it does not claim a detector-only comparison.

On July 15, the post-run Python checker focused suites and complete no-live suite pass.
The refreshed offline audit covered 30 RQ1 skills/90 properties, 10 RQ3 scenarios/30
public properties, and 308 saved Bash scripts (116 generated-development, 192
human-authored hidden). No property specification changed. The later live validation
added eight generated v1 Python diagnostics; none of their reported violations survived
manual review. Detailed findings are in
`2026-07-14-checker-suite-audit.md`; the whole-pipeline keep/simplify/remove review is in
`2026-07-14-pipeline-simplification-review.md`.

## 5. What is not finished

### 5.1 Required before protocol freeze

- **D1 image/freeze closure:** all 30 heavy environments and both model-specific overlays
  are built, locked, and runtime-audited. Promote their immutable input-tree,
  construction, generic Skillgen, and per-track image identities and change the manifest
  from draft to frozen only after the pilot/regression gates.
- **Final statistical freeze:** encode and test the exact bootstrap seed/resamples and
  output schemas for both RQ1 and RQ3, then hash them in the freeze manifest.
- **Frozen experiment schedules:** the 30×3 RQ1 and 10×3 RQ3 draft cells, one
  replication, resource limits, output paths, and derived scheduler seeds already pass
  completeness checks. Regenerate their frozen copies against the final protocol, suite,
  and image identities and reject any duplicate, omission, or hash drift.
- **Final independent review:** perform one fresh adversarial artifact review and repeat
  the same gates from the eventual clean frozen checkout.
- **Documentation reconciliation:** remove remaining historical claims in README/paper
  text that imply a baseline gap isolates one component, classify targeted findings by
  branch reach, or use raw distinct-property counts as the headline.
- **Artifact rehearsal:** perform a clean-checkout, sub-30-minute smoke path and verify
  every documented command and relative link.

### 5.2 Requires funded model access

The configurable patcher implementation is complete at the unit/integration level and
its guided Pi path has now completed a genuine saved-failure patch, independent exact
replay, and strict bounded-development RQ1 verification.
Campaign protocols freeze `random=direct`, `greybox=direct`, and `skillrace=pi`, with a
300-second patch timeout. RQ1 writes a patch-only ledger and only then launches the
separate exact replay; RQ3 resolves the same backend policy inside its confined public
repair child while retaining its historical combined receipt format for compatibility.
Both patchers are execution-blind and may change only `SKILL.md`; raw direct responses,
Pi traces, and repair rationales are not durable artifacts. A live paid Pi patch smoke
on 2026-07-13 reached GLM-4.5-Flash, produced a one-turn `SKILL.md` correction, and cost
⚡0.00031506. It exposed and led to removal of an over-strict campaign-metadata file-set
check; the corrected validator accepts the patch and checks only the revision-safe skill
package. This synthetic smoke was not replayed and is not result evidence. The July 14
live chain closed the mechanical patch/replay gate but returned `same_failure`, not a
confirmed repair. It exposed two invalid generated checkers: one imposed JSON stdout
absent from a DataFrame task, and one invoked the wrong callable with an incompatible
argument. The patch/replay implementation is no longer the immediate blocker. The
minimal checker self-audit and full offline suite review now pass; the remaining live
blocker is the absence of an eligible current-format saved failure.

A later fresh DeepSeek-V3.2 SkillRACE cell produced one counted failure with two
property signatures; one signature reproduced against the unchanged skill. The Pi
patcher then exhausted its 300-second limit on redundant inspection calls without
editing. The ledger failed closed and the real bounded RQ1 verifier counted zero defects.
This led first to an edit-only prompt and then to the guided SDK read→reason→edit backend
documented in the July 14 handoff. That backend completed a non-reused patch operation
with full accounting and a separate replay. A new positive repair gate should be
attempted only on a different, manually defensible failure. The only remaining non-json
saved failure is a timeout whose generated initial validator already solves its task, and
older smoke failures use obsolete checker/campaign contracts. No paid call was made
rather than forcing an invalid gate.
Timed-out Pi usage is also snapshotted before forced container removal, preventing
already-flushed token and cost records from disappearing during cleanup.

- Generate twenty zero-shot RQ3 base skills under the `/2` journal schema—ten fresh
  private scenario copies per model track. Historical unprovenanced template skills fail
  closed and cannot be relabelled or reused.
- The complete RQ1-style engineering path is now live-validated by a two-execution V3.2
  gate: proposal, Pi, checker, per-failure patch, patched exact replay, unchanged-skill
  confirmation, and recursive development analysis. Both post-search replays timed out,
  so the gate makes no defect claim. If the final hardcoded model inventory changes, run
  the same bounded gate for the replacement route. Add only a bounded RQ3 component smoke
  if that distinct pipeline still lacks live coverage.
- The first five-cell attempt reached both models but no agent execution: universal
  realization/build failures exhausted four cells, and the fifth was stopped to avoid
  waste. That diagnostic caused exact failure/cost preservation and sequential frozen
  retry semantics to be implemented. A subsequent two-execution DeepSeek/SkillRACE
  component smoke reached and completed Pi twice, then exposed the checker-entrypoint bug
  described above; its regression is fixed. The later bounded gate completed every
  post-search phase and fixed a relative campaign-path defect without repeating its two
  search executions. The generic RQ1-style implementation gate is no longer outstanding.
- Yunwu connectivity is verified for both selected models. The dated public rate extract,
  custom-credit policy, direct reasoning receipts, exact single-model catalogs, Pi 0.73.1
  images, and successful multi-turn thinking/tool traces are archived and offline-audited.
  Remaining paid work before freeze is the bounded development-only campaign pilot (and
  only any narrowly necessary RQ3 component smoke) after image/runtime closure.

### 5.3 Scientific measurements and publication artifact

- Freeze the protocol, datasets, model/role settings, prompts, images, one replication,
  resources, journal policy, and analysis hashes before looking at headline outcomes.
- Run all RQ1 and RQ3 campaigns, every per-failure patch/replay, and data-dependent
  distinct-group confirmations.
- Generate paper tables/figures from verified artifacts and report mixed/negative results
  unchanged. There is currently no evidence-backed claim that SkillRACE wins.
- Complete the anonymized conference package, archival metadata/DOI plan, and final paper
  consistency pass. Current paper result fields remain placeholders.

## 6. Reviewer commands

Use the repository virtual environment; the host has no bare `python` command:

```bash
# Fast, no-cost artifact gate
PYTHON=.venv/bin/python scripts/artifact_smoke.sh

# D1 selection, source, license, and image audit
.venv/bin/python -m skillrace.d1_audit \
  experiments/manifests/rq1-skills.draft.json --require-images

# D2 contracts and stored runtime evidence
.venv/bin/python -m skillrace.scenario_contract validate \
  scenarios --require-runtime-evidence

# Complete no-live regression suite
.venv/bin/python -m pytest -m 'not live'

# One journaled Yunwu connectivity probe
.venv/bin/python scripts/yunwu_hello.py
```

Do not start paid headline commands while `STATUS.md` says the protocol is draft. A
passing offline suite proves implementation consistency; it does not prove experimental
effectiveness.
