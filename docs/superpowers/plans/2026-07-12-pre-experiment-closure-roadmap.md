# SkillRACE pre-experiment closure roadmap

**Status date:** 2026-07-13  
**Scope:** finish and freeze the design and implementation before any headline run.  
**Authoritative protocol:**
[`2026-07-11-skillrace-evaluation-design.md`](../specs/2026-07-11-skillrace-evaluation-design.md),
amended by
[`2026-07-12-dual-model-experiment-design.md`](../specs/2026-07-12-dual-model-experiment-design.md).

This is the current engineering roadmap. Earlier Qwen, Pi 0.62, 120-run, shared-seed,
extra-ablation, or five-family-pilot ideas are superseded.

## Frozen design we are implementing

- The current draft selection runs the complete evaluation twice as independent model
  tracks: `glm-4.5-flash` and `deepseek-v4-flash` through Yunwu. `deepseek-v3.2` is a
  development-validated candidate; before headline work, hardcode the final inventory.
  Any added model must receive a complete independent track and the same preflight,
  pricing, image, schedule, and analysis inputs before freeze.
- Within a track, use that one model for every model-driven role. Report tracks
  separately; never pool their runs, defects, pass rates, or provider-credit costs.
- RQ1 uses 30 public skills and three methods: Random, VeriGrey-inspired L1, and
  SkillRACE. Each method/skill/model cell gets 30 counted agent executions. Random uses
  30 independent proposals; the adaptive methods use 10 bootstrap plus 20 exploration
  executions.
- RQ2 is diagnostic evidence from the same SkillRACE runs. Intended-branch reach is not
  required for bug credit; useful alternate exploration and serendipitous defects count.
- RQ3 uses 10 fixed scenarios. Each model independently generates one fresh base skill
  per scenario. The same three producers each spend 30 public executions, then one
  identical reviser consumes each method's byte-bounded feedback. The unrevised base and
  three revisions each take the same ten-test hidden exam exactly once.
- Every raw failed public execution receives one independent patch of the original skill
  and one exact-case replay. SkillRACE's patcher receives bounded native reasoning,
  tree, guard, and branch evidence; baseline patchers receive the shared failure core.
  Repair never changes search state or RQ3 aggregate revision feedback.
- One representative of each deduplicated failure group is separately rerun against the
  unchanged skill. Reproduction is intermediate evidence; a group enters headline
  repair-validated yield only when its already-required independent patch also makes the
  representative exact replay pass every originally failed property.
- The fixed execution count is 8,000 across both tracks: 5,400 RQ1, 1,800 RQ3 public,
  and 800 RQ3 hidden. Repairs add 0--7,200 executions; confirmations add one per suspected
  distinct group. All extra work and cost are reported outside the 30-run search budget.
- Paid development artifacts are never reused as headline observations. We may fix
  generic infrastructure or universal schema defects found by pilots, but do not tune a
  method to a particular skill's outcome.

## Completed implementation

- [x] Durable, crash-safe 30-run campaign engine with deterministic bounded parallelism,
  transactional coordinates, immutable receipts, and fail-closed unknown outcomes.
- [x] Random, globally fixed VeriGrey-inspired L1, and reasoning/tree/guard-guided
  SkillRACE information boundaries and generation paths.
- [x] Pre-run candidate realization, build, sanity, and mechanically compiled property
  checks shared by all methods.
- [x] Fresh networkless per-check execution, explicit pass/fail/inconclusive states, and
  receipt-backed defect grouping and unchanged-skill confirmation.
- [x] Exactly one original-skill patch and exact-case replay for every raw public failure,
  integrated before grouped confirmation in RQ1 and RQ3.
- [x] Strict RQ1/RQ2 and RQ3 analyzers that rehash their inputs and generate machine-owned
  JSON/CSV/TeX/plot data.
- [x] D1 continuation from the historical 22-skill boundary to 30. The July 12 search
  freezes the 628-row popularity-ordered pool, dispositions every row through the stop
  point, and stops at the first eight additional strict admits. Source, license,
  property, applicability, family, and contingency records are machine-audited.
- [x] D2's 10 scenarios, 100 hidden tests, 192 checks, reference overlays, starting-state
  negatives, and assigned negative implementations.
- [x] Public/hidden RQ3 staging, equal feedback envelopes, three comparable revisions,
  strict 4-by-10 hidden grading, and recursive result verification.
- [x] Private per-track RQ3 preparation: exactly one journaled base-generation call per
  scenario/model, normalized cross-track benchmark identity, and resume without duplicate
  provider calls.
- [x] Yunwu provider integration, redacted exactly-once journals, exact usage and custom
  provider-credit accounting, dated rate-card evidence, and direct/Pi reasoning probes
  for both selected model IDs.
- [x] Pi 0.73.1 model-specific images and compatibility settings. Each image exposes only
  its track model; the GLM tool-result compatibility issue seen under Pi 0.62 is resolved.
- [x] Complete draft RQ1 and RQ3 schedules for both tracks, including unique cells,
  derived scheduler seeds, separate output roots, and shared API/Docker/agent limits.
- [x] RQ3 driver refuses draft headline schedules before scenario preparation or any
  provider call. All ten private preparations finish before any public campaign begins.

## Active closure work

### 1. Finish immutable execution images

- [x] Complete all 30 D1 construction images, including the three dependency-heavy
  environments.
- [x] Apply both tiny model overlays to every construction image and retain immutable
  construction/final image IDs and input-tree hashes.
- [x] Validate all 30 skills and all 62 model execution images (60 D1 plus two generic
  Skillgen overlays) from their lock records.

Gate:

```bash
python3 -m skillrace.d1_images --out experiments/image-locks --validate --require-images
python3 -m skillrace.d1_audit experiments/manifests/rq1-skills.draft.json --require-images
```

### 2. Regenerate D2 runtime evidence

The old 100-test oracle matrix was intentionally invalidated before changing from the
old generic runtime to Pi 0.73.1 model-neutral templates. This prevents stale evidence
from being silently relabelled.

- [x] Rebuild and execute every reference, empty starting state, and assigned negative
  in fresh containers, with at most three Docker jobs concurrently.
- [x] Persist the new image-bound validation records.
- [x] Require all 100 records to pass structural and runtime verification: 100/100
  references pass, 100/100 starting states are rejected, all 215 assigned negative pairs
  are killed, and root validation reports zero pending or failed records.

Gate:

```bash
python3 -m skillrace.scenario_contract validate scenarios --require-runtime-evidence
```

### 3. Run bounded development-only pilots

- [x] Preserve the first diagnostic attempt separately. It reached both selected models
  but produced zero agent starts, exposed collapsed realization diagnostics and unsafe
  parallel retry coordinates, and was stopped with one explicitly non-resumable unknown
  development operation. See
  `experiments/development-pilots/2026-07-12/campaign-smoke-diagnostic.md`.
- [x] Complete a two-execution DeepSeek/SkillRACE component smoke. It reached Pi and
  completed both agent runs, exercising proposal/build/sanity, Pi reasoning traces,
  trace folding, SkillRACE fallback selection, repair bookkeeping, and the explicit
  development-only zero-confirmation ledger. It found no defect and is excluded from all
  headline results. Its state-property checks exposed an inherited-entrypoint bug in
  checker children; that exact Docker condition is now covered by a passing regression.
- [x] Attempt the checked-in GLM/V3.2 five-cell development smoke. It was intentionally
  stopped rather than completed after exposing cross-cutting realization, checker-file
  staging, and generated-oracle validity defects. Two separate V3.2 diagnostic roots
  reached Pi with native reasoning; they are not pooled or treated as method evidence.
  See `experiments/development-pilots/2026-07-13/glm-v32-five-cell-diagnostic.md`.
- [x] Run one fresh bounded post-fix development gate through a real failure, patch,
  exact replay, unchanged-skill confirmation, and recursive analysis. The V3.2/Random
  gate used two counted executions; both post-search replays timed out, so it validates
  plumbing but yields zero repair-validated defects. It also exposed and fixed
  workspace-relative campaign path resolution. See
  `experiments/development-pilots/2026-07-13/bounded-development-gate.md`.
- [ ] Live-validate the post-v6 edit-only Pi patcher and 32,000-byte compact SkillRACE
  trace envelope on one new predeclared genuine failure. The v6 search and unchanged-
  skill confirmation were valid, but its patch timed out before editing, so it produced
  no exact patched replay and no confirmed defect. Do not repatch the saved v6 failure.
- [ ] If the final hardcoded model inventory differs from the already exercised routes,
  run the same bounded gate for the replacement model before headline work. Do not
  expand this into ten executions unless a final model/method path still lacks live
  coverage.
- [ ] Exercise both `glm-4.5-flash` and `deepseek-v4-flash`, all three methods across the
  schedule, provider journals, image routing, campaign accounting, repair-before-
  confirmation, and resumability.
- [x] Inspect only cross-cutting infrastructure: malformed receipts, wrong model/image,
  leakage, scheduling, accounting, recovery, or universal prompt/schema failures.
- [ ] If needed, run one bounded end-to-end RQ3 smoke through preparation, public
  production, revision, and one hidden execution per model. Do not run the full RQ3
  benchmark or reuse its output as headline data.

Current provider note: on July 13, `deepseek-v4-flash` returned HTTP 429 with Yunwu's
explicit “current group upstream load is saturated” response for the bare provider-minimal
request, the non-thinking request, and the thinking-enabled request. The failed fresh
component run therefore stopped before candidate construction and consumed zero agent
slots. This is recorded as a provider-preflight failure rather than evidence about any
method; it does not justify changing the selected model or tuning a prompt.

Development fallback note: `deepseek-v3.2` passed direct and Pi tool-use probes with
native reasoning after an explicit DeepSeek thinking compatibility mapping. It remains a
development candidate until the final model inventory is hardcoded and all archival
rate, image, protocol, schedule, and analysis inputs are produced for it.

Development outputs live under `out/development-pilots/` or
`experiments/development-pilots/`, are labelled non-headline, and are excluded from the
frozen result roots.

### 4. Freeze the artifact

- [ ] Change the two protocol identities from draft to frozen only after image, D2, and
  pilot gates pass.
- [ ] Generate frozen D1/RQ1/RQ3 manifests and schedules that refer only to immutable
  inputs, model catalogs, and image IDs.
- [ ] Write one recursive freeze manifest covering code/dependency identity, models and
  rate evidence, prompts/policies, D1/D2 data, properties/applicability, images,
  schedules/seeds/resources, journal policy, and analysis code.
- [ ] Prove no headline result directory exists before freeze and make launch scripts
  reject draft or hash-mismatched inputs before spending provider credits.

### 5. Final verification and rehearsal

- [x] Run the full no-live suite and the offline artifact gate with required images. The
  July 13 gate passed compilation, D1's 30 required images, Yunwu rate/probe evidence,
  and D2's 10 scenarios/100 hidden tests/192 checks.
- [ ] Repeat the same gates once from the eventual clean frozen checkout.
- [ ] Rehearse the documented smoke/verification path from a clean checkout without
  changing frozen inputs.
- [ ] Reconcile README, status, reviewer guide, paper methodology, and commands with the
  actual frozen manifests. Keep result tables explicitly placeholder-only.
- [ ] Record every intentional skip or environment prerequisite; do not describe an
  engineering test as an experiment result.

Final pre-headline gate:

```bash
python3 -m pytest -m 'not live'
python3 -m compileall -q skillrace tests scripts
PYTHON=python3 scripts/artifact_smoke.sh
python3 -m skillrace.d1_images --out experiments/image-locks --validate --require-images
python3 -m skillrace.scenario_contract validate scenarios --require-runtime-evidence
git diff --check
```

## After this roadmap: headline execution

Only the recursive frozen-manifest gate may create headline output roots. Independent
cells may then run concurrently under the predeclared resource pool. Each model track is
completed, verified, analyzed, and reported separately. Any unknown external outcome
leaves its coordinate incomplete; it never triggers an unrecorded retry. Paper tables
and figures are generated only from recursively verified artifacts, including mixed or
negative results.
