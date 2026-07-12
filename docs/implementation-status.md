<a href="../README.md"><img src="../skillrace-icon.png" alt="SkillRACE" width="54" align="right"></a>

# SkillRACE implementation status

**Status date:** 2026-07-12

**Measurement status:** no headline RQ1 or RQ3 experiment has been run.

**Protocol status:** the main protocol and dataset manifest are still `draft`.

This page answers two questions: what has actually been built and verified, and what is
still required before the ISSTA measurements can begin. For the complete experimental
contract—including what counts as a run, fairness controls, failure/defect calculations,
RQ3 grading, cost accounting, parallelism, and validity limitations—read
[evaluation-reviewer-guide.md](evaluation-reviewer-guide.md). The approved detailed
design is [the evaluation specification](superpowers/specs/2026-07-11-skillrace-evaluation-design.md).

Historical files under `docs/superpowers/plans/` record how implementation work was
organized. They are not the current experiment protocol; where they mention extra
ablations, six RQ3 conditions, three hidden repeats, a per-skill Greybox sweep, or shared
seeds, the lean evaluation guide and specification supersede them.

## 1. Current bottom line

The campaign engine, three testing methods, isolated property checker, public datasets,
RQ3 revision/evaluation pipeline, durable model journal, and deterministic parallel
execution machinery are implemented. The most important missing implementation is the
**headline RQ1 analysis path**: the repository can detect, group, and confirm findings,
but its old `aggregate.py` still counts raw property IDs and must not produce paper
numbers.

The remaining closure work is therefore not “run the expensive experiment now.” It is:

1. finish and test the RQ1 confirmed-defect analysis and table/plot generator;
2. run one clean repository-wide regression and independent artifact review;
3. create and hash the concrete one-replication experiment schedules;
4. replenish CloseAI, run a small multi-family paid pilot, and inspect its complete raw
   receipts without tuning to an evaluated skill;
5. freeze code, prompts, model settings, images, inputs, resources, and analysis; and
6. only then run the paid headline campaigns.

## 2. Experiment that the implementation supports

| Study | Conditions | Allocation | Fixed agent executions |
|---|---|---|---:|
| RQ1: public skill testing | Random, VeriGrey-inspired L1, SkillRACE on 22 skills | 30 per method/skill | 1,980 |
| RQ2: mechanism | Labels from the same SkillRACE RQ1 executions | no extra arm | 0 extra |
| RQ3 public testing | the same three producers on 10 base skills | 30 per producer/scenario | 900 |
| RQ3 hidden final exam | zero-shot plus three feedback revisions on 10×10 tests | each test once | 400 |

The lean design has one predeclared campaign replication. Random spends all 30 counted
executions on fresh independent cases. VeriGrey-inspired and SkillRACE each spend ten on
independently generated bootstrap cases and 20 on guided exploration. The intended fixed
total is 3,280 agent executions, plus one data-dependent confirmation rerun per distinct
suspected defect. Base generation, internal semantic processing, check compilation, and
revision are model calls whose cost is recorded but which do not masquerade as agent
executions.

## 3. What is implemented

### 3.1 Fair campaign protocol and three methods

- `experiments/protocols/issta-main.draft.json` fixes `qwen3.6-flash`, budget 30,
  bootstrap count 10, five pre-agent attempts per coordinate, one global L1 Greybox
  schema, and the shared generator configuration.
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
- Natural-language properties are compiled into mechanical scripts before the agent
  execution. The compiler may see the task, initial tree, tools, immutable image, and
  applicability policy; it cannot see the future trace, final state, or verdict.
- Compile identity binds model/prompt/policy, properties, candidate, applicability, and
  image digest. Legacy post-run check authoring is excluded from headline evidence.
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
  property/signature recurring with status `confirmed` counts; timeout, error,
  inconclusive, or not-reproduced remains separate.
- Confirmation consumes time and money but not one of the 30 search slots. Its full cost
  is recorded. This confirmation machinery is integrated in RQ3 and is intended to feed
  the unfinished RQ1 analysis.

### 3.6 Deterministic parallel execution

- Independent method/skill/replication cells can run concurrently through one manifest
  scheduler and global resource pool with separate API, Docker, and agent limits.
- Random and Greybox use transactional frozen batch reservations.
- SkillRACE freezes tree version N and a property-first, branch-diverse target plan for a
  bounded epoch (default four); workers execute immutable jobs, then one reducer folds
  them in candidate-ID order into N+1.
- Reverse worker-completion tests require byte-identical campaign, tree, generator,
  cache, guard, and classification state. Partial generation intent can be recovered
  without double-spending a coordinate.
- Hidden tests and checks are currently isolated but sequential inside a scenario. RQ3
  scenarios are independent and can be scheduled outside one another, but a final frozen
  top-level RQ3 schedule is not yet checked in.

### 3.7 D1 public skill suite

- The draft headline set contains 22 redistributable public skills, 60 properties, and
  12 families: 18 high- and four medium-environment-contingency skills.
- These were selected as the first 22 that satisfied the approved inclusion/exclusion
  protocol under the mining order (by popularity). The same methodology and filters remain
  in force for selecting the remaining 8 headline skills.
- Four original skills used during development are excluded from headline inference.
  Three mined public candidates are excluded because redistribution permission is absent
  or unsafe.
- Twenty-five source records pin repository, commit, path, source hash, fidelity, and
  license evidence; 18 upstream license texts are embedded. All 22 declared base images
  currently resolve under the D1 audit.
- Contingency was classified before results and is never a post-result inclusion rule.

### 3.8 D2 skill-generation suite

- Ten scenarios contain exactly ten hidden tests each: 100 tests and 192 executable
  criteria in total.
- Each test has a versioned public/hidden contract, Dockerfile and check hashes, a
  reference overlay, and assigned negative implementations.
- Runtime evidence regenerated on 2026-07-12 records 100/100 references passing,
  100/100 empty starting states rejected, and all 215 assigned negative/criterion pairs
  killed. This is **100 validated Docker evidence records**; all 192 checks run inside
  the built containers, with every criterion evaluated in a fresh container.
- The negative implementations validate the oracles only. They are never counted as bugs
  found by any testing method.

### 3.9 RQ3 public phase, revision, and hidden exam

- A scenario stages only its public purpose/campaign package and one journal-provenance
  `/2` zero-shot base skill.
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

### 3.11 CloseAI operation journal

- Calls use stable operation IDs and exact request hashes with atomic/fsynced starts,
  attempts, terminals, and call-terminal receipts.
- Provider identifiers are hashed; secrets and raw provider IDs are not stored.
- Missing usage/cost is represented as unknown rather than false zero. Production pricing
  fails closed for an unknown model.
- Strict `/2` base/revision validators reject missing or inconsistent model, request,
  usage, cost, billing, provider, operation, or journal provenance.
- `scripts/closeai_hello.py` is a one-call diagnostic that uses the same durable journal;
  it is not an experiment.

## 4. Verification evidence available now

The following are targeted engineering checks, not scientific outcomes:

- campaign accounting, recovery, information-boundary, frozen-epoch, and reverse-order
  tests have passed in focused suites;
- all 100 D2 runtime-evidence records validate with zero pending or failed audits;
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

## 5. What is not finished

### 5.1 Required before protocol freeze

- **Headline RQ1 analysis:** replace the legacy raw-property `aggregate.py` path with a
  verifier that consumes campaign and confirmation receipts, reconstructs failure groups,
  calculates confirmed yield/discovery/AUC/censoring, performs paired family-cluster
  uncertainty, and writes all RQ1/RQ2 tables and plots without hand editing.
- **D1 completion:** continue the same fixed candidate-order inclusion protocol to add 8
  additional public code-behavior skills (target headline suite = 30), then rerun D1
  draft/frozen validation checks to confirm the manifest is no longer draft.
- **Final statistical freeze:** encode and test the exact bootstrap seed/resamples and
  output schemas for both RQ1 and RQ3, then hash them in the freeze manifest.
- **Concrete experiment schedules:** after adding 8 more D1 skills, generate the 30×3 RQ1 and
  10×3 RQ3 public campaign cells with one replication, resource limits, output paths,
  and derived scheduler seeds; validate that no duplicate or omitted cell exists.
- **Final independent review:** perform one fresh adversarial artifact review and repeat
  the same gates from the eventual clean frozen checkout.
- **Documentation reconciliation:** remove remaining historical claims in README/paper
  text that imply a baseline gap isolates one component, classify targeted findings by
  branch reach, or use raw distinct-property counts as the headline.
- **Artifact rehearsal:** perform a clean-checkout, sub-30-minute smoke path and verify
  every documented command and relative link.

### 5.2 Requires funded model access

- Regenerate all ten zero-shot RQ3 base skills under the `/2` journal schema. Old `/1`
  artifacts intentionally fail closed and cannot be relabelled.
- Run a small, explicitly development-only multi-family pilot through generation, agent
  execution, checking, confirmation, feedback, revision, and hidden grading. Inspect
  receipts and generic failure modes; do not tune prompts to a headline skill.
- CloseAI currently returns HTTP 403 consistent with insufficient balance. No completion
  call in the current build conversation succeeded; `/v1/models` connectivity alone is
  not a paid model result.

### 5.3 Scientific measurements and publication artifact

- Freeze the protocol, datasets, model/role settings, prompts, images, one replication,
  resources, journal policy, and analysis hashes before looking at headline outcomes.
- Run all RQ1 and RQ3 campaigns and data-dependent confirmations.
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

# One journaled connectivity probe after funding is restored
.venv/bin/python scripts/closeai_hello.py
```

Do not start paid headline commands while `STATUS.md` says the protocol is draft. A
passing offline suite proves implementation consistency; it does not prove experimental
effectiveness.
