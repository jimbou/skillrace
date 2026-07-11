# Analysis and Artifact Freeze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the ISSTA protocol before headline inspection and produce method-blind confirmed-defect, discovery, survival, cost, calibration, ablation, and RQ3 analyses directly from verified raw artifacts.

**Architecture:** A verifier first converts raw violations into blinded adjudication packets and requires a committed failure-cause grouping. Pure analysis modules consume only verified manifests plus that grouping. A freeze command hashes every experimental input and refuses dirty or post-result protocols; one artifact command rebuilds all tables/figures from raw records.

**Tech Stack:** Python 3.12, pytest, JSON/CSV, SHA-256, bootstrap resampling, LaTeX, Docker for the smoke artifact.

---

> **Lean-protocol override:** The full experiment has exactly two baselines plus
> SkillRACE. The only permitted small ablation is outcomes-only SkillRACE on five frozen
> skills. Confirmation is one rerun per deduplicated suspected defect.

## File map

- Create `skillrace/defect_triage.py`: confirmation and method-blind cause packets.
- Create `skillrace/analyze_rq1.py`: yield curves, AUC, censored discovery, family-aware uncertainty, cost.
- Create `skillrace/analyze_calibration.py`: RQ2/component agreement and stability report.
- Create `skillrace/freeze_protocol.py`: input manifest creation and verification.
- Create `skillrace/artifact.py`: smoke and full reproduction commands.
- Create `tests/test_defect_triage.py`, `tests/test_analyze_rq1.py`, `tests/test_freeze_protocol.py`, `tests/test_artifact.py`.
- Create `experiments/manifests/d1.json`, `d2.json`, `ablations.json`, and `calibration.json`.
- Create `analysis/defect-adjudication.schema.json` and, after blinded review, `analysis/defect-adjudication.json`.
- Create `scripts/artifact_smoke.sh`.
- Modify `skillrace/aggregate.py`: compatibility wrapper around the new analysis.
- Modify `paper/skillrace.tex`: frozen methodology and generated result includes.
- Modify `README.md` and `docs/implementation-status.md`: honest readiness state and artifact commands.

### Task 1: Confirm and blind candidate defects

**Files:**
- Create: `skillrace/defect_triage.py`
- Create: `tests/test_defect_triage.py`
- Create: `analysis/defect-adjudication.schema.json`

- [ ] **Step 1: Write confirmation and blindness tests**

```python
from skillrace.defect_triage import confirmed_candidates, blind_packet


def test_confirmation_requires_all_three_frozen_reruns():
    records = [{
        "candidate_id": "c1",
        "violated": ["p1"],
        "regrade": {"k": 3, "reproduced": {"p1": 3}},
    }, {
        "candidate_id": "c2",
        "violated": ["p2"],
        "regrade": {"k": 3, "reproduced": {"p2": 2}},
    }]
    assert [(item["candidate_id"], item["property_id"])
            for item in confirmed_candidates(records)] == [("c1", "p1")]


def test_blind_packet_contains_no_method_or_search_specific_fields():
    candidate = {
        "method": "skillrace", "candidate_id": "c1", "property_id": "p1",
        "skill": "demo", "task": "fix", "environment": "broken parser",
        "failure_summary": "bad exit code", "guard": "secret", "tree_version": 4,
    }
    packet = blind_packet(candidate, salt="frozen-review-salt")
    text = str(packet).lower()
    for forbidden in ["skillrace", "method", "guard", "tree_version"]:
        assert forbidden not in text
```

- [ ] **Step 2: Implement confirmation and packet export**

The frozen primary confirmation rule is the same property violation in all three reruns of the identical saved case. Preserve 1/3 and 2/3 findings as flaky/partially reproducible secondary records. Packet IDs are salted SHA-256 values over skill, candidate hash, property, and failure-evidence hash. Packets include replay instructions, prompt/environment, check provenance/script hash, normalized error/output/diff evidence, and adjudication questions, but no method, provenance source, branch, guard, coverage, output path, or candidate ID.

The schema requires two independent labels: `valid_defect` and `cause_cluster`. Disagreements receive a reconciled label and rationale. Equivalent failures share one cluster even when several properties fire; unrelated causes stay separate even under one property ID.

- [ ] **Step 3: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_defect_triage.py -q`

Expected: confirmation and blindness tests pass.

```bash
git add skillrace/defect_triage.py tests/test_defect_triage.py analysis/defect-adjudication.schema.json
git commit -m "feat: blind and confirm defect candidates"
```

### Task 2: Compute the RQ1 headline and secondary metrics correctly

**Files:**
- Create: `skillrace/analyze_rq1.py`
- Create: `tests/test_analyze_rq1.py`
- Modify: `skillrace/aggregate.py`

- [ ] **Step 1: Write exact small-data tests**

```python
from skillrace.analyze_rq1 import discovery_curve, normalized_auc, survival_record


def test_discovery_curve_counts_distinct_cause_clusters():
    events = [(1, "d1"), (2, "d1"), (4, "d2")]
    assert discovery_curve(events, budget=5) == [1, 1, 1, 2, 2]


def test_auc_is_normalized_by_budget_and_maximum_observed_yield():
    assert normalized_auc([0, 1, 1, 2]) == 0.5


def test_survival_record_is_one_based_and_right_censored():
    assert survival_record([False, True, False]) == {"time": 2, "observed": True}
    assert survival_record([False, False, False]) == {"time": 3, "observed": False}
```

- [ ] **Step 2: Implement per-campaign records**

For each counted exploratory execution, join confirmed/adjudicated defect clusters and emit a right-continuous distinct-defect curve. Primary yield is distinct confirmed cause clusters divided by counted exploratory executions. Confirmation reruns are reported as separate validation executions and in an all-in cost denominator; they never change discovery ordinal. Also emit raw findings, confirmed candidates, clusters, runs with any violation, intended/different/no-divergence/path-miss rates, targeted/serendipitous counts, invalid/repaired/rejected/fallback rates, fixed/compiled/inconclusive oracle counts, tokens, dollars, CPU, and wall time.

Define normalized AUC as the mean of the curve divided by the maximum final yield across compared methods for that skill/replication; if all final yields are zero, report AUC zero with `all_zero: true`.

- [ ] **Step 3: Add family-aware uncertainty**

Read family and contingency from the frozen D1 manifest. Bootstrap by sampling families with replacement, then skills within sampled families, keeping all paired methods and replications together. Use a committed RNG seed and at least 10,000 resamples for final intervals. Report paired SkillRACE-minus-random and SkillRACE-minus-greybox estimates with 95% intervals; show every per-skill value and do not hide negative effects.

The D1 manifest marks publicly mined skills `headline_eligible: true` only when their recorded source/permalink satisfies the frozen mining protocol. Locally authored skills are `headline_eligible: false` and appear only in a separately labelled controlled-case-study table. Low-contingency public skills remain in the headline and are never filtered after outcomes are known.

Time-to-first uses Kaplan–Meier-compatible `(time, observed)` records; do not take the median only among successful campaigns. If the median is not reached, report `not reached` plus restricted mean discovery time at the common budget.

- [ ] **Step 4: Keep `aggregate.py` as a compatibility CLI**

Make it call the verified RQ1 loader/analyzer and emit a deprecation note. Remove raw-property-ID headline counting and observed-only time medians.

- [ ] **Step 5: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_analyze_rq1.py -q`

Expected: curve, AUC, censoring, and clustered-resampling fixture tests pass.

```bash
git add skillrace/analyze_rq1.py skillrace/aggregate.py tests/test_analyze_rq1.py
git commit -m "feat: analyze confirmed RQ1 defect yield"
```

### Task 3: Predeclare ablations and component calibration

**Files:**
- Create: `experiments/manifests/ablations.json`
- Create: `experiments/manifests/calibration.json`
- Create: `skillrace/analyze_calibration.py`
- Create: `tests/test_calibration_manifest.py`

- [ ] **Step 1: Encode the exact subset before results**

`ablations.json` names a representative development-excluded subset stratified by family and contingency, fixed budgets/replications, and these arms: uniform SkillRACE frontier, outcomes-only SkillRACE, direct property-guided generation, matched seeded no-feedback, matched seeded greybox, and a whole-pipeline model-strength swap. It records that the same model is swapped for agent and every model-driven role.

`calibration.json` fixes trace sampling, human boundary labels, merge-pair labels, check-verdict audit samples, tree seed-order stability permutations, adjudicator procedure, and metrics before labels are inspected.

- [ ] **Step 2: Analyze component validity**

Extend existing calibration scores with boundary F1, merge agreement/kappa, guard executability and intended-condition satisfaction, check false-positive/false-negative rates against human audit, tree branch stability across seed orders, and disagreement rates. Report these as construct-validity evidence, not as tuned headline outcomes.

- [ ] **Step 3: Test manifests and commit**

Run: `.venv/bin/python -m pytest tests/test_calibration_manifest.py -q`

Expected: manifests reference only frozen/development subsets as appropriate and every arm has a budget/model policy.

```bash
git add experiments/manifests/ablations.json experiments/manifests/calibration.json skillrace/analyze_calibration.py tests/test_calibration_manifest.py
git commit -m "docs: preregister ablations and calibration"
```

### Task 4: Freeze every experimental input before headline runs

**Files:**
- Create: `skillrace/freeze_protocol.py`
- Create: `tests/test_freeze_protocol.py`
- Create: `experiments/manifests/d1.json`
- Create: `experiments/manifests/d2.json`

- [ ] **Step 1: Write hash-verification tests**

Create a temporary protocol with one skill and one scenario file, freeze it, and assert verification succeeds. Modify one byte and assert verification names that path and fails. Add an `out/` result file before freeze and assert production freeze refuses to proceed.

- [ ] **Step 2: Implement freeze and verify commands**

The freeze manifest records git commit, dirty status, protocol JSON/hash, selected model and all role settings, prompt-version constants, D1/D2 file hashes, public-versus-local eligibility, skill families/contingency, property/applicability hashes, scenario/oracle evidence hashes, base/expert skill hashes, Dockerfile and resolved image digests, budgets, bootstrap counts, replications, greybox level, generator settings, resource limits, regrade rule, ablation/calibration manifests, analysis source hashes, RNG derivation, timestamp, and environment versions.

Production freeze requires a clean worktree, all acceptance tests, all D2 oracle audits, all required images, and no headline output directory. It changes protocol status from `draft` to `frozen` without changing substantive settings, writes `experiments/frozen/issta-main-v1.json`, and prints the local annotated tag command `git tag -a issta-protocol-v1 -m 'freeze ISSTA protocol v1'` for deliberate execution.

Every production campaign verifies the frozen manifest before generating a candidate and writes its freeze hash into `campaign.json`. Analysis rejects mixed freeze hashes.

- [ ] **Step 3: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_freeze_protocol.py -q`

Expected: clean freeze, drift detection, and preexisting-result refusal tests pass.

```bash
git add skillrace/freeze_protocol.py tests/test_freeze_protocol.py experiments/manifests/d1.json experiments/manifests/d2.json
git commit -m "feat: freeze and verify experiment inputs"
```

### Task 5: Build one command that reproduces tables and figures

**Files:**
- Create: `skillrace/artifact.py`
- Create: `tests/test_artifact.py`
- Create: `scripts/artifact_smoke.sh`
- Modify: `paper/skillrace.tex`

- [ ] **Step 1: Write artifact-output contract tests**

With small fake RQ1/RQ3/calibration fixtures, run the artifact builder and assert it creates:

```text
artifacts/generated/rq1-campaigns.csv
artifacts/generated/rq1-summary.json
artifacts/generated/rq1-macros.tex
artifacts/generated/rq1-discovery.pdf
artifacts/generated/rq3-paired.csv
artifacts/generated/rq3-summary.json
artifacts/generated/rq3-macros.tex
artifacts/generated/calibration.json
artifacts/generated/costs.csv
artifacts/generated/provenance.json
```

Run twice and assert byte-identical CSV/JSON/TeX plus plot data; PDF metadata may be normalized before comparison.

- [ ] **Step 2: Implement `smoke`, `verify`, and `build` modes**

`verify` checks freeze/manifests/raw hashes and adjudication completeness. `build` generates every table, macro, and plot from raw data without manual copy/paste. `smoke` uses replayed traces and fake model/agent fixtures to exercise all three methods, one deterministic epoch, one RQ3 feedback/revision stub, two hidden tests, and analysis in under 30 minutes with no API key.

`scripts/artifact_smoke.sh` creates a clean temporary output root, runs static/offline tests, scenario structure validation, replay campaign smoke, RQ3 smoke, artifact build, and LaTeX compilation. It exits nonzero on a missing artifact, dirty generated diff, or unresolved result macro.

- [ ] **Step 3: Include generated TeX rather than editing numbers**

Have `paper/skillrace.tex` input `artifacts/generated/rq1-macros.tex` and `rq3-macros.tex`. Methodology text names the full-system headline, matched-feedback ablation, independent adaptive initializations, seedless random, one globally frozen greybox level, confirmation/grouping rules, censoring, and family-aware analysis. Claims stay conditional until verified full results exist.

- [ ] **Step 4: Run and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_artifact.py -q
bash scripts/artifact_smoke.sh
```

Expected: tests pass and smoke completes in under 30 minutes.

```bash
git add skillrace/artifact.py tests/test_artifact.py scripts/artifact_smoke.sh paper/skillrace.tex
git commit -m "feat: reproduce paper artifacts from raw results"
```

### Task 6: Run the pre-headline acceptance audit

**Files:**
- Modify: `README.md`
- Modify: `docs/implementation-status.md`

- [ ] **Step 1: Execute every non-live gate**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m skillrace.scenario_contract validate scenarios
.venv/bin/python -m skillrace.freeze_protocol verify --draft experiments/protocols/issta-main.draft.json
bash scripts/artifact_smoke.sh
git diff --check
git status --short
```

Expected: all commands pass and the worktree is clean.

- [ ] **Step 2: Execute the five-family pilot**

Run a frozen small-budget pilot containing at least one debugging, CLI, parser, SQL, and low-contingency skill. Require complete runner/checker/cost artifacts; branch, fallback, validation/rejection, timeout, and inconclusive rates; successful resume; and no unrecoverable infrastructure error.

- [ ] **Step 3: Audit without optimizing the headline result**

Use the pilot only to repair correctness, infrastructure, or protocol ambiguity. Do not change skills, properties, method parameters, exclusions, or analysis because one method performs poorly. Any necessary protocol change increments the version and reruns the complete pilot before freeze.

- [ ] **Step 4: Freeze, tag locally, and update readiness status**

After explicit model selection and user review, run the production freeze command and create the local annotated protocol tag. Update status docs from “implementation ready” to the exact achieved gate; do not state that SkillRACE outperforms until verified headline results support it.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs/implementation-status.md experiments/frozen
git commit -m "docs: freeze ISSTA evaluation protocol"
```

## Plan 6 completion gate

The expensive run may start only from a clean commit carrying a verified frozen manifest and protocol tag. Full results are accepted only if all campaigns share that freeze hash, blinded defect adjudication is complete, analysis rebuilds deterministically, the artifact smoke passes, and the paper imports generated—not hand-entered—numbers.
