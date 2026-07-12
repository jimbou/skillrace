# SkillRACE evaluation guide for reviewers

**Status:** design contract for the lean ISSTA evaluation, updated 2026-07-12.
Machine manifests are still marked `draft`; no headline result has been run.

This document explains what is compared, what consumes experimental budget, how a
failure becomes a confirmed skill defect, what information each method may use, how
parallel execution preserves search semantics, and which parts of the artifact remain
unfinished. The detailed approved specification is
[`superpowers/specs/2026-07-11-skillrace-evaluation-design.md`](superpowers/specs/2026-07-11-skillrace-evaluation-design.md).

## 1. Claims and research questions

SkillRACE is presented as **concolic execution for coding-agent skills**. A concrete
agent run is segmented into behavior episodes, folded into a tree, and used to derive
reasoning/outcome branch conditions. SkillRACE mutates those conditions to seek
property-relevant unexplored behavior. Its constraints are approximate natural-language
conditions rather than exact symbolic formulas; intended branch reach is measured but is
not required for a discovered defect to count.

The experiment asks:

- **RQ1 — defect yield:** Under the same 30-agent-execution budget, does SkillRACE find
  more distinct confirmed skill defects than independent Random generation and a
  VeriGrey-inspired tool-sequence-guided method?
- **RQ2 — mechanism:** Within the same SkillRACE runs, how often does a mutation reach
  its intended branch, find a different new branch, fail to diverge, or miss its target,
  and which defects are targeted versus serendipitous?
- **RQ3 — skill generation:** Does revising one zero-shot generated skill with feedback
  from each testing method improve its pass rate on independently authored hidden tests?

RQ2 uses labels from the RQ1 runs. It is not an additional experiment or ablation. The
lean study has no direct-property, outcomes-only, matched-seed, uniform-frontier, extra
model, or per-skill parameter arm.

## 2. Experimental units and budgets

The primary RQ1 cell is one `(method, public skill, replication)` campaign. The planned
lean manifest has one predeclared replication per method/skill because agent executions
are expensive; all three methods are nevertheless evaluated on every selected skill.
The cell budget is exactly 30 counted agent executions.

| Term | Meaning | Consumes the 30-run search budget? |
|---|---|:---:|
| Generation attempt | One attempt to propose, realize, build, and sanity-check a case | No, unless Pi subsequently starts |
| Counted agent execution | Pi was durably recorded as started on a valid case | Yes |
| Bootstrap execution | One of the first ten adaptive executions used to initialize search state | Yes |
| Exploration execution | A fresh Random case or adaptive guided case after initialization | Yes |
| Oracle execution | Mechanical checks over the completed agent state | No additional agent slot |
| Confirmation execution | One post-campaign rerun of a deduplicated suspected defect | No; recorded and costed separately |
| RQ3 hidden execution | One condition taking one hidden final-exam test | Separate RQ3 evaluation budget |

The paper and artifact avoid using the bare word *run* where it could mean several of
these units. In result files, `execution` means a Pi agent was durably started,
`attempt` means candidate production before that boundary, `campaign` means one complete
method/skill cell, and `replication` means an independently initialized repeat of that
campaign. The lean design uses **one campaign replication**, not repeated campaigns with
different random seeds. This choice limits stochastic precision and is reported as a
threat to validity; it buys breadth over all 22 public skills within the available cost.

### Planned fixed workload

Provided the draft manifests are frozen unchanged, the fixed workload is:

| Study phase | Calculation | Counted agent executions |
|---|---:|---:|
| RQ1 public-skill testing | 22 skills × 3 methods × 30 | 1,980 |
| RQ3 public testing of base skills | 10 scenarios × 3 methods × 30 | 900 |
| RQ3 hidden final exam | 10 scenarios × 4 conditions × 10 tests | 400 |
| **Fixed total** | | **3,280** |

This total excludes a data-dependent number of confirmation executions, because there is
one confirmation for each distinct suspected failure group. It also excludes ten
zero-shot base-generation calls, thirty revision calls, and the methods' internal model
calls; those are not Pi executions but are fully included in cost and wall-clock reports.
There are no hidden-test repeats, extra baseline arms, ablations, or model sweeps in the
headline study.

The durable Pi started marker is the budget boundary. A build, schema, policy, or sanity
failure before that marker is a **generation failure**: the method may try another
candidate without advancing from, for example, execution 12 to 13. These failures,
attempt counts, generation time, and model cost are still reported. Once Pi starts, a
normal completion, agent error, timeout, lost external outcome, or oracle-inconclusive
run consumes the slot. This prevents a method from hiding expensive bad executions.

Each execution coordinate and attempt coordinate is unique and reserved transactionally.
The default maximum is five pre-agent attempts per execution coordinate. A completed
campaign must contain exactly 30 distinct counted coordinates and terminal durable
receipts; incomplete campaigns are not headline eligible.

## 3. Methods under comparison

### Random: 0 bootstrap + 30 fresh cases

Random asks the shared model for a new task/environment at every iteration. It has no
initial corpus and never mutates a prior execution. It may retain only a description
digest to discourage a literal duplicate. It cannot read traces, tools, reasoning,
episodes, properties, outcomes, verdicts, guards, or tree state when generating a case.
Every accepted Random case records `independent_test: true` provenance.

### VeriGrey-inspired: 10 bootstrap + 20 guided cases

The baseline adapts the feedback mechanism in VeriGrey to skill correctness testing:

1. independently generate and execute ten bootstrap cases;
2. retain all ten, including duplicate tool sequences;
3. initialize coverage from every bootstrap execution;
4. expose only globally frozen **L1 schematized tool events**;
5. award one energy unit for each new tool, transition, and full sequence; and
6. select retained cases using this novelty/energy state and generate 20 mutations
   conditioned on their tool sequences.

The baseline cannot read reasoning, outcomes, properties, episode purposes, guards, or
SkillRACE tree state. Later mutants enter the retained corpus only when they add covered
behavior. The experiment calls it **VeriGrey-inspired**, not VeriGrey: VeriGrey's
injection-specific context bridging and injection oracle do not transfer, so both are
replaced with the shared skill-agnostic realization and correctness checker.

### SkillRACE: 10 bootstrap + 20 concolic cases

SkillRACE independently generates ten bootstrap cases. It segments concrete traces,
summarizes observation-grounded outcomes, builds a behavior tree, extracts branch
conditions, chooses property-relevant targets, and synthesizes 20 guided cases.

Mutation is opportunistic. It may change several coherent environment features. A case
that misses its motivating branch is not discarded: a different new branch is useful
exploration, and a reproducible property violation remains eligible regardless of the
branch that exposed it. The recorded motivation includes target node, guard, mutation,
target property, validation outcome, realized branch outcome, and property relationship.

## 4. What is shared and what is intentionally different

Every method uses the same:

- `qwen3.6-flash` model for the agent and every model-driven role;
- frozen generation/realization/build/repair configuration;
- base image for a given skill;
- non-semantic candidate sanity gate;
- Pi runner, wall-clock limit, and runtime integrity checks;
- property specifications, applicability policy, check compiler, and fixed checker;
- provider retry/journal policy; and
- total counted agent-execution budget.

The adaptive methods need initialization; Random does not. Their ten bootstrap sets are
generated independently using the same frozen bootstrap protocol rather than sharing one
possibly favorable corpus. Random's 30 fresh cases are also independently generated.
Provider stochastic outputs therefore need not be identical. A recorded scheduler RNG
seed makes reservations and replay reproducible, but it is not a shared test-case seed.

The comparison is a **full-system comparison**. In particular, the Random-to-VeriGrey
gap does not causally isolate feedback because their corpus/mutation structures also
differ. SkillRACE must beat both practical reference systems, but the paper will not
attribute a gap to one component without an experiment that was actually run.

Equal agent executions do not imply equal total model calls: SkillRACE performs extra
segmentation/tree/guard calls because those calls are part of the technique. All model
tokens, dollars, generation failures, CPU time, and wall time are secondary efficiency
outcomes so this overhead remains visible.

## 5. Input validity and oracle independence

Every built case passes a shared mechanical gate before Pi can start:

- candidate schema and path confinement;
- image build success and base-runtime integrity;
- required tools/workspace artifacts;
- task invocation and syntax;
- check schema/syntax; and
- starting task not already solved when mechanically decidable.

SkillRACE may additionally validate its proposed branch condition, because directed
condition validation is part of the technique. Baselines do not receive the condition or
correctness properties for generation.

Natural-language properties are compiled into Bash scripts **before the agent run**. The
compiler sees the property, prompt, initial workspace tree, available tools, and immutable
image identity; it never sees the eventual trace, diff, final files, or verdict. The
compiler prompt, model, policy, applicability, candidate, properties, image digest, and
resulting script hashes form the compile identity.

After the run, the final filesystem is snapshotted once. Each script executes in a fresh
networkless child with all capabilities dropped, `no-new-privileges`, a process limit,
and a host timeout. A check cannot create evidence for another. Invalid scripts, missing
state, Docker errors, or timeout produce `holds: null` and are **inconclusive**, never a
fabricated pass or violation. The legacy post-run authoring mode is excluded from
headline evidence.

## 6. Failure, fault, violation, and defect definitions

These terms are deliberately separate:

- **Generation failure:** no agent started because proposal/build/sanity failed. It does
  not consume a search slot but is reported.
- **Infrastructure failure:** orchestration, Docker, provider, or artifact failure. If it
  occurs before Pi starts it is uncounted; after start it consumes a slot. It is not a
  skill defect.
- **Oracle-inconclusive:** required evidence was unavailable or untrustworthy. It
  consumes a slot if the agent started and is reported separately.
- **Property violation:** a precommitted fixed or compiled mechanical check returned
  definite `holds: false`. This is a suspected defect, not yet a unique confirmed defect.
- **Failure signature:** SHA-256 over the property ID and normalized mechanical failure
  detail, with volatile paths, addresses, and numbers removed.
- **Suspected defect group:** executions with the same skill, property, and failure
  signature.
- **Confirmed defect:** one replayable representative is rerun once after the campaign
  and produces the same property/signature again.
- **Not reproduced:** confirmation completed but did not produce the same signature; it
  does not count in confirmed yield.

A latent flaw in `SKILL.md` is a *fault* in the conceptual sense, but the automated
experiment cannot directly count source-level fault locations. Its measurable proxy is a
confirmed, cause-grouped defect. The paper must therefore say “confirmed distinct
defects” or “confirmed failure-cause groups,” not claim that every group is a uniquely
located textual fault.

Failure signatures are fully mechanical. The implementation lowercases the checker
detail, collapses whitespace, replaces hexadecimal addresses, multi-component absolute
paths, and numeric literals with stable placeholders, truncates the result to 500
characters, and hashes `(property_id, normalized_detail)` using canonical JSON and
SHA-256. The grouping key additionally includes the skill. Normalizing volatile literals
can merge two failures that differ only by a number, so this is intentionally conservative
against inflated defect counts. Raw unnormalized details remain in the evidence bundle.
The exact normalization function and its source hash must be frozen before results.

One property failing in five executions is therefore one defect group, not five defects.
Two genuinely different failure causes for the same property may remain separate because
their normalized signatures differ. Failure grouping and confirmation occur after the
30-run search; methods do not receive confirmation results during search.

The deterministic representative is the earliest counted execution carrying that
skill/property/signature. It is rerun exactly once with the same case and checker. A
confirmation error, timeout, or inconclusive verdict does not count as confirmed and is
reported under its own status; it is not retried until it happens to pass. If a crash
leaves it unknown whether that external rerun happened, recursive verification stops and
the experiment cell is incomplete rather than issuing a possibly duplicate paid call.

The deliberately wrong implementations stored with RQ3 scenarios are **oracle-validation
faults**. They prove that hidden checks reject known bad behavior and never count as
SkillRACE discoveries or headline defects.

## 7. RQ1 and RQ2 outcomes

The primary RQ1 metric is distinct confirmed-defect yield at 30 counted executions,
reported per skill and aggregated with skill-family-aware uncertainty. Planned secondary
outputs are:

- confirmed-defect discovery curve and area under it;
- one-based executions to first confirmed defect, right-censored at 30 when none occurs;
- executions containing any definite violation;
- confirmation success rate;
- generation rejection, fallback, timeout, agent-error, and oracle-inconclusive rates;
- fixed-versus-compiled finding provenance; and
- model tokens, dollars, CPU time, and wall time.

More precisely, for method `m`, skill `s`, and counted prefix `n`, let
`D(m,s,n)` be the number of distinct `(skill, property, failure_signature)` groups whose
first occurrence is at or before execution `n` and whose one later confirmation has
status `confirmed`. Then:

- final yield is `D(m,s,30) / 30` confirmed defects per counted execution;
- the discovery curve is `D(m,s,n)` for `n = 1..30` (confirmation is attributed back to
  the first observed occurrence, never to the post-search confirmation time);
- curve area is the predeclared discrete mean
  `AUC30(m,s) = sum(D(m,s,n), n=1..30) / 30`;
- time to first is the smallest one-based `n` for which `D(m,s,n) > 0`; a campaign with
  none is right-censored at 30; and
- violation-run rate is the number of counted executions containing at least one
  definite violation divided by 30. Multiple checks failing in one execution do not
  create extra “runs with a violation.”

Every per-skill value and every negative SkillRACE-minus-baseline difference will be
reported. The primary comparisons are paired differences on the same 22 skills:
SkillRACE minus Random and SkillRACE minus VeriGrey-inspired. The intended analysis uses
at least 10,000 family-cluster bootstrap resamples with the frozen analysis RNG seed;
each sampled family retains all its skills and all three paired methods. It reports the
mean paired effect and 95% interval, while pooled counts are descriptive rather than
pretending that 60 properties or 1,980 executions are independent observations. The
headline analysis implementation and its final frozen seed are still unfinished, so this
procedure must be encoded, tested, and hashed before any headline result is inspected.

RQ2 labels are produced only for SkillRACE and never gate defect eligibility:

- `intended_branch`: the run reached its target and created the intended new child;
- `different_new_branch`: it missed the target but added another new branch;
- `no_divergence`: it reached the area without creating a new child;
- `path_miss`: the target prefix was not reached; and
- `unfolded`: branch evidence could not be computed.

A violated property is `targeted` when its property ID equals the mutation's selected
target property; otherwise it is `serendipitous`. Branch reach and property relationship
are separate fields.

The current legacy `aggregate.py` still summarizes raw violated property IDs and is **not
approved for headline results**. The remaining RQ1 analysis task must consume confirmation
ledgers, group failure causes, produce family-aware uncertainty, and generate every paper
table/plot without manual editing.

## 8. RQ3 skill-generation experiment

There are ten scenarios, each with a public purpose/campaign package and ten hidden tests.
The hidden suite totals 100 tests and 192 executable criteria.

For one scenario:

1. Generate one zero-shot base skill with `qwen3.6-flash` and retain its exact prompt,
   request bytes, hashed provider identities, usage, cost, stable operation ID, and
   immutable terminal journal receipts.
2. Test that same base skill with Random, VeriGrey-inspired, and SkillRACE under the same
   30-run allocation as RQ1.
3. Deduplicate suspected findings and confirm one representative per failure signature
   outside the search budget.
4. Project each method into the same ordered **3,600 canonical-JSON UTF-8 byte** feedback
   schema. Deterministic section round-robin prevents verbose generic summaries from
   erasing all method-specific evidence. Actual provider tokens are recorded separately;
   the byte cap is not described as a token cap.
5. Make one revision call per producer. System/user templates, base skill, model,
   temperature, reasoning setting, and output budget are identical; only the envelope
   differs. The zero-shot skill is not revised.
6. Run four versions—zero-shot and the three revisions—once on each of the ten hidden
   tests, for an exact 4×10 matrix.

A hidden test passes only if the verdict set contains exactly the unique criterion IDs in
its contract, all carry hidden-independent provenance, and all hold. Missing, duplicate,
extra, or wrong-provenance criteria make the test inconclusive rather than allowing a
partial pass. Strict pass additionally requires applicable fixed invariants. The
headline denominator is all ten scheduled tests: timeout, error, missing, or
inconclusive outcomes add no pass to the numerator and are also reported by status.

All four conditions bind the same test contract, Dockerfile/check hashes, resolved base
image digest, model, and wall-clock limit into their request identity. Recursive
verification reloads the current scenario, requires exactly `t1..t10`, rehashes raw
launch/run/trace/verdict/cost artifacts, recomputes grades, and rejects a stored result
whose evidence or grade changed.

The primary RQ3 outcome is each revision's paired hidden-test pass-rate change from the
zero-shot version. SkillRACE-versus-baseline contrasts, strict pass, cost, and
per-scenario effects are secondary. Scenarios—not 100 tests—are the top-level units for
aggregation.

For scenario `s` and condition `c`, the functional pass rate is
`passes(s,c) / 10`, where the numerator counts only scheduled tests whose complete exact
criterion set holds. An error, timeout, missing result, or inconclusive grade contributes
zero to that numerator but remains separately visible; the denominator never shrinks.
The paired revision effect is
`delta(s,c) = pass_rate(s,c) - pass_rate(s,zero-shot)`. The primary summary for each
revision is the mean of the ten scenario-level deltas, with a 95% paired bootstrap
interval obtained by resampling the ten scenarios as intact blocks. All ten deltas are
shown so a pooled gain cannot hide a scenario regression. The 100 individual tests are
not treated as 100 independent samples. Functional exact-criterion pass is primary;
`strict_pass`, which additionally requires applicable fixed invariants, is secondary.

## 9. Hidden-information boundary

Campaign generation, confirmation, feedback construction, and revision may read only the
public stage. The production public phase runs in a fail-closed Linux bubblewrap
namespace with an empty root and explicit read-only runtime/code/public mounts. Only
campaign/revision output and the per-phase durable ledger are writable. The source
scenario tree and `tests/` are not mounted, so an absolute-path read fails. The exact
binary hash/version, argv, mounts, environment-variable names, and policy hash are stored
and verified on resume.

The campaign process retains host networking for CloseAI and the trusted Docker Unix
socket. The socket is an explicit trust boundary: trusted SkillRACE orchestration could
ask the host daemon to mount another host path, but generated agents never receive the
socket and current campaign commands mount only recorded public paths. The artifact tests
absolute hidden reads from both campaign and revision roles and records this limitation
rather than claiming a stronger sandbox than it provides.

Only after the public phase has terminal receipts and passes a hidden-byte/path scan does
the parent process resolve the hidden test directory and start evaluation.

## 10. Parallel execution

Parallelism is available but cannot change logical search results:

- Independent method/skill/replication cells run under one manifest scheduler.
- A global `ResourcePool` separately caps concurrent API, Docker, and agent operations.
- Random proposals are reserved transactionally in independent batches.
- VeriGrey-inspired batches are reserved from one frozen greybox state and folded by
  deterministic coordinate.
- SkillRACE freezes tree version N and one property-first, branch-diverse target plan for
  an epoch (default requested epoch size four). Workers synthesize/run immutable cases;
  one reducer folds completed results in candidate-ID order into version N+1.
- Every proposal, external start/terminal event, result, cleanup, and fold has an immutable
  receipt. Durable generation intent permits rollback of unpublished partial adaptive
  state after a crash.

Integration tests reverse worker completion order and require byte-identical tree, cache,
guards, classifications, generator snapshot, and campaign manifest. The outer experiment
driver treats a cell as successful only when it returns `complete: true` and terminal
`status: completed`; an incomplete campaign cannot be mislabeled successful.

Hidden RQ3 executions and individual property checks are currently correctness-isolated
but scheduled sequentially inside one scenario pipeline. They are safe future
parallelization opportunities, not implemented speedup claims. Parallel execution is not
an experimental arm or ablation; wall-clock and resource peaks are reported.

## 11. Crash safety, replay, and cost accounting

Before an external model/agent action, the artifact writes a durable start/intent. After
the action it writes an immutable terminal result and receipt using locked, atomic,
fsynced storage. If the process dies after an action may have started but before terminal
evidence exists, the outcome is `unknown`; the system stops rather than silently paying
for a second call.

CloseAI requests use stable operation IDs, exact frozen request bytes/hashes, redacted
provider identifier hashes, strict model/usage validation, known or explicitly unknown
billing, retry receipts, and a permanent ledger. Production pricing fails closed for an
unknown model. Missing provider usage/cost is never converted to zero.

Campaign cost separates generation, compilation, and agent cost. Confirmation remains
outside the 30-run budget but its executions, tokens, dollars, and wall time are reported.
RQ3 reports search, confirmation, revision, hidden evaluation, and inclusive total cost.

## 12. Datasets and anti-cherry-picking boundary

RQ1's draft headline manifest currently contains the first 22 redistributable public
code-behavior skills that passed the approved inclusion protocol (in the order of mining
popularity). This set is from 12 families: 18 high- and four medium-contingency.
Four original development skills
are excluded because they were used while building/tuning the system. Three public
candidates are excluded because their redistribution terms are absent or unsafe; their
content is not shipped. Source commits, paths, hashes, fidelity, license evidence, and 18
embedded upstream license files are machine audited.

The 22 skills expose 60 predeclared natural-language properties. Under the same protocol,
we still need to add eight more qualifying skills before the dataset is finalized.

| Family | Headline skills (property count) |
|---|---|
| API | `fastapi-endpoint` (2) |
| CLI | `cli-argparse-fix` (3), `cli-subcommand-validator` (3) |
| Async testing | `condition-based-waiting` (5) |
| Config | `yaml-config` (2) |
| Debugging | `debugging-difficult-bugs` (2), `systematic-debugging` (2) |
| Git workflow | `finishing-a-development-branch` (2), `using-git-worktrees` (2) |
| Parser | `json-parser` (3), `parser-generator` (2) |
| Refactor | `code-refactor-fowler` (3), `refactor` (3), `refactor-complexity-reduce` (3) |
| Regex | `regex-expert` (3) |
| SQL | `sql-queries` (3), `sql-query-generator` (3), `sql-query-json` (3), `sqlmodel-orm` (3) |
| Testing process | `test-driven-development` (4) |
| Unit-test generation | `unit-test-generation` (2), `unit-test-generator` (2) |

The exact family, contingency, image, source, license, property, and applicability files
are the authority; this table is only a human-readable projection.

Environment contingency was classified before results and is not an inclusion filter.
All legally admissible prepared public skills remain in the headline manifest regardless
of pilot outcome.

RQ3 has exactly ten scenarios × ten tests. Each criterion has a reference overlay and
assigned negative implementations. The current Docker audit records 100/100 references
passing, 100/100 starting states rejected, and all 215 assigned negative/criterion pairs
killed. Every criterion was audited in a fresh container. Evidence is bound to contract,
script, overlay, Docker, and image identities.

| RQ3 scenario | Hidden tests | Executable criteria |
|---|---:|---:|
| `argparse-cli` | 10 | 26 |
| `config-parser` | 10 | 26 |
| `csv-stats` | 10 | 26 |
| `fix-failing-test` | 10 | 20 |
| `interval-merge` | 10 | 11 |
| `json-csv` | 10 | 12 |
| `log-parser` | 10 | 11 |
| `regex-validate` | 10 | 30 |
| `sqlite-query` | 10 | 20 |
| `text-template` | 10 | 10 |
| **Total** | **100** | **192** |

## 13. Protocol freeze and prohibited researcher degrees of freedom

Before a paid headline run, the artifact must freeze and hash:

- code and dependency environment;
- D1/D2 manifests and all skill/property/scenario inputs;
- model and role configurations;
- prompts and feedback/check policies;
- base/case image digests;
- budgets, bootstrap count, attempt cap, epoch size, resource limits, and one replication;
- global VeriGrey L1 schema; and
- analysis code and statistical procedure.

The team will not select a Greybox level per skill, add/drop skills after seeing results,
rerun only unfavorable cells, tune a prompt to one benchmark skill, or substitute a
stronger model for one role/method. Development-only skills may be used for general
pipeline debugging. Headline directories may be created only from a hash-verified frozen
protocol; draft/pilot outputs remain visibly separate.

## 14. Validity and fairness limitations

The protocol controls many avoidable biases, but it does not erase these limitations:

- **One stochastic replication.** One campaign per method/skill cannot estimate
  run-to-run variance well. The study prioritizes 22-skill breadth and paired comparisons
  under a fixed budget; claims must be about this model/configuration and must show every
  skill, not universal superiority.
- **A single model.** Using `qwen3.6-flash` everywhere removes cross-method model
  confounding and cost explosion, but limits external validity to other models. No claim
  of model-independence is supported.
- **Full-system rather than component causality.** SkillRACE receives properties for
  target selection and pays for additional semantic calls; Greybox sees only L1 tool
  events; Random sees no execution feedback. These are the techniques being compared,
  not equal-information ablations. A gap supports end-to-end effectiveness, not that one
  internal component caused it.
- **VeriGrey is adapted.** Injection-specific mutation/context bridging and its injection
  oracle do not apply to general skill correctness. Calling the method
  “VeriGrey-inspired” and publishing the exact L1 adapter prevents a false replication
  claim, but conclusions about the original VeriGrey system must remain limited.
- **Independent initialization.** Adaptive methods draw different bootstrap cases from
  the same frozen generator distribution; Random has no artificial seed corpus. This
  avoids coupling all methods to one lucky corpus but adds stochastic imbalance. Exact
  inputs and costs are retained for audit.
- **Oracle construct validity.** Properties cover predeclared mechanically checkable
  behavior, not every notion of skill quality. Pre-run compilation and negative/reference
  audits reduce hindsight and false-verdict risk, while inconclusive evidence never
  becomes a pass or defect. Raw scripts and verdicts permit reviewer inspection.
- **Conservative cause grouping.** Literal normalization may merge related but distinct
  failures; one confirmation may miss intermittent defects. Both choices tend to reduce
  yield rather than multiply it, and status/raw evidence remain public.
- **Hidden tests execute once.** RQ3's scheduled denominator is honest about timeout and
  error, but one execution per test cannot estimate the agent's success probability on
  that test. The inference unit is the scenario and the claim is a paired one-shot final
  exam under the frozen model.
- **Isolation is not a hostile-container security proof.** Bubblewrap removes hidden
  paths from public roles, but trusted orchestration retains the Docker socket. Generated
  agents do not receive it, commands and mounts are recorded, and the limitation is
  explicit.
- **Dataset representativeness.** Legal redistribution and mechanical-oracle requirements
  necessarily exclude proprietary, credentialed, and presentation-heavy skills. Family
  clustering, source provenance, and full inclusion decisions make this boundary visible
  but cannot make the suite representative of every skill ecosystem.
- **No result-guarantee.** Engineering choices are intended to give SkillRACE a legitimate
  opportunity to exploit its semantic guidance while keeping baselines fair. They do not
  guarantee that it wins; mixed or negative outcomes must be reported unchanged.

## 15. Implemented, in progress, and unfinished

### Implemented and locally verified

- Exact 30-run campaign accounting and method information boundaries.
- Crash-safe sequential and frozen-epoch parallel campaign execution.
- Deterministic reverse-completion replay.
- D1 selection/licensing/image audit for 22 public skills.
- D2 structural and fresh-container runtime audit for 10 scenarios, 100 tests, 192
  checks, and 215 assigned negative pairs.
- Pre-run compiled property checks and isolated execution.
- Durable redacted CloseAI journal and `/2` base/revision provenance.
- RQ3 confirmation, equal byte-bounded feedback, and exactly-once revision/hidden
  execution primitives.
- Offline artifact smoke script and explicit requirements/status documents.

### Recently integrated and focused-verified

- Strict exact-criterion/raw-evidence/image-digest RQ3 verification and 4×10 analysis.
- Bubblewrap public-phase barrier covering campaign, confirmation, and revision roles.
- Central RQ3 integration of `/2` journal-linked base/revision validators.
- Confirmation-inclusive RQ3 cost columns and cross-producer feedback-policy equality.

The combined repository suite reported 562 passed and 100 skipped on 2026-07-12 after
these integrations; compilation and diff checks were also clean. A separate clean-checkout
artifact rehearsal remains unfinished.

### Not finished

- Final independent adversarial rereview after the RQ3 integrations above.
- Headline-ready RQ1 defect grouping/confirmation analysis, family-aware uncertainty,
  plots, and table generator. Current `aggregate.py` is diagnostic only.
- Frozen main experiment/analysis manifests and archive hashes.
- A clean-checkout, sub-30-minute artifact rehearsal and final documentation consistency
  pass.
- Ten zero-shot RQ3 base-skill regenerations with `/2` provenance.
- Any current live multi-family Qwen/Pi pilot: CloseAI returns HTTP 403 consistent with
  insufficient balance.
- All headline RQ1/RQ3 executions and result tables. No claim that SkillRACE wins has yet
  been measured.

## 16. Reviewer reproduction gates

The no-cost first gate is:

```bash
PYTHON=.venv/bin/python scripts/artifact_smoke.sh
```

The D1/D2 evidence gates are:

```bash
.venv/bin/python -m skillrace.d1_audit experiments/manifests/rq1-skills.draft.json --require-images
.venv/bin/python -m skillrace.scenario_contract validate scenarios --require-runtime-evidence
```

The complete offline suite is:

```bash
.venv/bin/python -m pytest -m 'not live'
```

Paid commands must not be used until `STATUS.md` says the manifests are frozen and the
CloseAI account is funded.
