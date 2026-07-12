# RQ3 Revision and Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the complete leakage-safe skill-generation experiment: equal-budget testing feedback, normalized revision, six hidden-evaluation conditions, three repeats, and one linked manifest per scenario/replication.

**Architecture:** Phase 1 runs only inside a public staged scenario. A method-neutral projector converts campaign artifacts into the same bounded JSON envelope. A condition-blind reviser produces three skills. Hidden evaluation is a separate process that receives one condition at a time and writes immutable per-test/per-repeat records; an RQ3 reducer links hashes and computes paired outcomes.

**Tech Stack:** Python 3.12, pytest, Docker, Pi/CloseAI, JSON, SHA-256, deterministic canonical-JSON UTF-8 byte accounting.

> **Implemented protocol clarification (2026-07-11).** The earlier `cl100k_base`
> plan below is superseded: using an OpenAI tokenizer to describe a Qwen prompt would
> be scientifically misleading. Feedback envelopes now record
> `budget_unit=canonical-json-utf8-bytes/1`, `max_bytes`, and `used_bytes`. Actual Qwen
> provider tokens remain separate revision-call cost fields. The primary RQ3 outcome
> is each revision's paired change from zero-shot; SkillRACE-versus-baseline contrasts
> are secondary. Errors, timeouts, inconclusive, and missing hidden executions are
> conservative non-passes in the all-scheduled-tests headline, with available-case
> sensitivity reported separately.

---

> **Lean-protocol override:** The active evaluation has four conditions—zero-shot,
> random-feedback, VeriGrey-feedback, and SkillRACE-feedback—and executes every hidden
> test once. `no-skill`, `expert`, and three-repeat probability estimation are deferred.

## File map

- Create `skillrace/feedback.py`: fixed-schema, token-bounded feedback envelopes.
- Create `skillrace/rq3.py`: public staging, phase orchestration, linked manifests.
- Create `skillrace/analyze_rq3.py`: paired pass-probability summaries.
- Create `tests/test_feedback.py`: schema/order/budget tests.
- Create `tests/test_rq3_leakage.py`: public staging and forbidden-path tests.
- Create `tests/test_rq3_manifest.py`: six-condition/three-repeat linkage tests.
- Keep envelope accounting dependency-free by using exact canonical-JSON UTF-8 bytes.
- Modify `skillrace/revise_skill.py`: consume envelopes and record complete revision identity.
- Modify `skillrace/skill_eval.py`: no-skill condition, repeats, strict metric, immutable outputs.
- Modify `skillrace/run_case.py`: omit Pi's `--skill` flag for the native-agent condition.
- Modify `skillrace/check_properties.py`: label independently authored hidden criteria explicitly.
- Modify `scripts/run_experiment.py`: expose an RQ3 public-campaign entry point without hidden paths.
- Modify `scenarios/README.md`: exact end-to-end commands and output structure.

### Task 1: Physically stage the public-only scenario for Phase 1

**Files:**
- Create: `skillrace/rq3.py`
- Create: `tests/test_rq3_leakage.py`

- [ ] **Step 1: Write failing allowlist tests**

```python
import pathlib

from skillrace.rq3 import stage_public_scenario


def test_public_stage_contains_only_approved_groups(tmp_path):
    source = pathlib.Path("scenarios/json-csv")
    staged = stage_public_scenario(source, tmp_path / "public")
    assert {path.name for path in staged.iterdir()} == {
        "scenario.md", "base_skill", "campaign", "public-stage.json"
    }
    assert not (staged / "tests").exists()
    assert not (staged / "expert_skill").exists()


def test_public_files_do_not_contain_hidden_test_paths_or_hashes(tmp_path):
    source = pathlib.Path("scenarios/json-csv")
    staged = stage_public_scenario(source, tmp_path / "public")
    combined = "\n".join(path.read_text(errors="ignore") for path in staged.rglob("*") if path.is_file())
    assert "/tests/" not in combined
    assert "tests/t" not in combined
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_rq3_leakage.py -q`

Expected: import fails.

- [ ] **Step 3: Implement an allowlist copy with a public manifest**

```python
from __future__ import annotations

import json
import pathlib
import shutil

from .io_utils import atomic_write_json, file_hash


PUBLIC_ENTRIES = ("scenario.md", "base_skill", "campaign")


def stage_public_scenario(source, destination):
    source = pathlib.Path(source).resolve()
    destination = pathlib.Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    for name in PUBLIC_ENTRIES:
        item = source / name
        target = destination / name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    files = {
        str(path.relative_to(destination)): file_hash(path)
        for path in sorted(destination.rglob("*")) if path.is_file()
    }
    atomic_write_json(destination / "public-stage.json", {
        "schema": "rq3-public-stage/1",
        "source_scenario_id": source.name,
        "files": files,
    })
    return destination
```

The public manifest deliberately omits the complete `scenario.json`, because that file names hidden and expert artifact groups. Phase 1 subprocess working directories receive only this staged path. Their command lines and environment contain no original scenario root.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_rq3_leakage.py -q`

Expected: staging tests pass.

```bash
git add skillrace/rq3.py tests/test_rq3_leakage.py
git commit -m "feat: stage public-only RQ3 campaigns"
```

### Task 2: Normalize every method into one bounded feedback envelope

**Files:**
- Create: `skillrace/feedback.py`
- Create: `tests/test_feedback.py`
- Modify: `pyproject.toml`

- [x] **Step 1: Freeze exact byte accounting**

Do not add a tokenizer dependency. Canonically serialize the envelope as UTF-8 and
record its exact byte length under `canonical-json-utf8-bytes/1`. The Qwen provider's
actual API input/output tokens are recorded separately after revision.

- [ ] **Step 2: Write schema and budget tests**

```python
from skillrace.feedback import build_feedback_envelope, envelope_byte_count


def campaign(method):
    return {
        "method": method,
        "iterations": [{
            "i": 0,
            "candidate_id": "c1",
            "provenance": {"task_nl": "fix parser", "env_nl": "missing key"},
            "violated": ["p1"],
            "reproducible": ["p1"],
            "inconclusive": [],
        }],
        "generator_state": {"novelty": {"tools": 3}},
    }


def test_all_methods_emit_identical_top_level_schema():
    envelopes = [build_feedback_envelope(campaign(method), max_bytes=2000)
                 for method in ("random", "greybox", "skillrace")]
    assert [list(value) for value in envelopes] == [list(envelopes[0])] * 3
    assert "method" not in envelopes[0]


def test_envelope_respects_accounting_token_cap():
    value = campaign("skillrace")
    value["iterations"] *= 100
    envelope = build_feedback_envelope(value, max_bytes=880)
    assert envelope_byte_count(envelope) <= 880


def test_confirmed_and_inconclusive_findings_are_separate():
    value = campaign("random")
    value["iterations"][0]["reproducible"] = []
    value["iterations"][0]["inconclusive"] = ["p2"]
    envelope = build_feedback_envelope(value, max_bytes=2000)
    assert envelope["confirmed_findings"] == []
    assert envelope["inconclusive_findings"]
```

- [ ] **Step 3: Implement deterministic projection and truncation**

Every envelope has this ordered schema:

```python
{
    "schema": "skillrace-feedback/1",
    "confirmed_findings": [],
    "explored_situations": [],
    "method_evidence": {"tool_novelty": [], "guard_mutations": [], "branch_outcomes": []},
    "inconclusive_findings": [],
    "accounting": {
        "budget_unit": "canonical-json-utf8-bytes/1",
        "max_bytes": max_bytes,
        "used_bytes": 0,
        "source_campaign_hash": "",
    },
}
```

Confirmed findings require the frozen reproduction threshold and include property, task, environment, reproduction count, replay case hash, and a method-neutral failure summary. Explored situations include candidate summaries for every counted execution. Method evidence populates only fields available to that method; unavailable fields stay empty. Inconclusive findings never appear as confirmed.

Sort by execution ordinal then stable hashes. Add items in priority order: confirmed findings, one explored-situation record per execution until its quota is full, method evidence, inconclusive findings. After each addition, canonical-serialize and count UTF-8 bytes; reject the addition if it exceeds the cap. Finally set `used_bytes` and verify the final object remains within the cap.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_feedback.py -q`

Expected: schema, budget, and separation tests pass.

```bash
git add pyproject.toml skillrace/feedback.py tests/test_feedback.py
git commit -m "feat: normalize bounded RQ3 feedback"
```

### Task 3: Make skill revision condition-blind and fully identified

**Files:**
- Modify: `skillrace/revise_skill.py`
- Create: `tests/test_revise_identity.py`

- [ ] **Step 1: Extract a pure revision request builder and test equality**

```python
from skillrace.revise_skill import revision_request


def envelope(evidence):
    return {
        "schema": "skillrace-feedback/1",
        "confirmed_findings": evidence,
        "explored_situations": [],
        "method_evidence": {"tool_novelty": [], "guard_mutations": [], "branch_outcomes": []},
        "inconclusive_findings": [],
        "accounting": {"budget_unit": "canonical-json-utf8-bytes/1", "max_bytes": 24000,
                       "used_bytes": 400, "source_campaign_hash": "abc"},
    }


def test_revision_request_differs_only_in_envelope_content():
    first = revision_request("# Skill\n", envelope([{"property": "p1"}]))
    second = revision_request("# Skill\n", envelope([{"property": "p2"}]))
    assert first[0] == second[0]
    assert first[1].replace("p1", "X") == second[1].replace("p2", "X")
```

- [ ] **Step 2: Replace plain-text/campaign-specific input with envelope-only input**

The CLI requires `--envelope`, validates its schema and token accounting, and never receives a method argument. Use the same system prompt, model, temperature zero, reasoning flag, and output limit for every revision. Copy only the base skill's allowed skill files into a new output directory; refuse a preexisting directory instead of deleting it.

Write `revision.json` with base-skill hash, envelope hash, system/user prompt hashes, prompt version, model configuration, raw response hash, revised skill hash, actual API input/output tokens, cost, and UTC timestamp. The record contains a blind `condition_id` supplied only after the model response is saved; the prompt never contains it.

- [ ] **Step 3: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_revise_identity.py -q`

Expected: request identity test passes.

```bash
git add skillrace/revise_skill.py tests/test_revise_identity.py
git commit -m "feat: revise skills from blind feedback envelopes"
```

### Task 4: Evaluate all six conditions with three repeats

**Files:**
- Modify: `skillrace/skill_eval.py`
- Modify: `skillrace/run_case.py`
- Modify: `skillrace/check_properties.py`
- Create: `tests/test_skill_eval_metrics.py`

- [ ] **Step 1: Write metric tests**

```python
from skillrace.skill_eval import grade_repeat, summarize_repeats


def test_functional_and_strict_grades_are_distinct():
    verdicts = [
        {"property_id": "behavior", "provenance": "hidden", "holds": True, "violated": False},
        {"property_id": "fixed-no-force-push", "provenance": "fixed", "holds": False, "violated": True},
    ]
    grade = grade_repeat(verdicts)
    assert grade["functional_pass"] is True
    assert grade["fixed_clean"] is False
    assert grade["strict_pass"] is False


def test_three_repeat_summary_reports_probability_and_stable_pass():
    result = summarize_repeats([
        {"functional_pass": True, "strict_pass": True},
        {"functional_pass": True, "strict_pass": True},
        {"functional_pass": False, "strict_pass": False},
    ])
    assert result["functional_pass_probability"] == 2 / 3
    assert result["stable_3_of_3"] is False


def test_no_skill_condition_omits_pi_skill_flag():
    from skillrace.run_case import pi_skill_flag
    assert pi_skill_flag("none", "json-csv") == ""
    assert pi_skill_flag("mounted", "json-csv") == "--skill /skills/json-csv"
```

- [ ] **Step 2: Implement condition and repeat semantics**

Support `--condition` choices `no-skill`, `base`, `random-feedback`, `greybox-feedback`, `skillrace-feedback`, and `expert`. `no-skill` derives a candidate with `skill_mode: "none"`, does not copy a skill to `/skills/<name>`, and records that absence. Add `pi_skill_flag(skill_mode, skill)` in `run_case.py`; omit Pi's `--skill` argument when the mode is `none`, and return the mounted path flag otherwise. All other conditions require a skill directory and its hash.

Add `--repeats` default three. Output paths are `runs/<test-id>/repeat-0`, `repeat-1`, and `repeat-2`. A functional pass requires at least one hidden criterion and all hidden criteria holding. `fixed_clean` requires no fixed violation. `strict_pass` requires both. Preserve timeout/error/inconclusive outcomes rather than coercing them to functional failures without status fields.

Pass `--check-provenance hidden-independent` from `skill_eval` into `check_properties`; explicit hidden scripts receive that provenance while the fixed core remains `fixed`. Summaries report per-test probability, stable 3/3, strict probability, condition totals, agent tokens/cost, wall time, and run IDs. Hidden prompts, Dockerfiles, and checks remain byte-identical across conditions.

- [ ] **Step 3: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_skill_eval_metrics.py -q`

Expected: metric tests pass.

```bash
git add skillrace/skill_eval.py skillrace/run_case.py skillrace/check_properties.py tests/test_skill_eval_metrics.py
git commit -m "feat: evaluate six RQ3 conditions with repeats"
```

### Task 5: Orchestrate Phase 1, revision, and hidden evaluation

**Files:**
- Modify: `skillrace/rq3.py`
- Create: `tests/test_rq3_manifest.py`

- [ ] **Step 1: Write a linked-manifest test using fake phases**

```python
from skillrace.rq3 import build_rq3_manifest


def test_manifest_links_all_conditions_and_three_repeats():
    manifest = build_rq3_manifest(
        scenario_id="json-csv",
        replication=1,
        base_skill_hash="base",
        campaign_records={name: {"hash": name} for name in ("random", "greybox", "skillrace")},
        envelopes={name: {"hash": name + "-env"} for name in ("random", "greybox", "skillrace")},
        revisions={name: {"hash": name + "-skill"} for name in ("random", "greybox", "skillrace")},
        evaluations={name: {"repeats": 3, "hash": name + "-eval"} for name in (
            "no-skill", "base", "random-feedback", "greybox-feedback",
            "skillrace-feedback", "expert",
        )},
        hidden_test_hashes={f"t{number}": str(number) for number in range(1, 11)},
    )
    assert set(manifest["evaluations"]) == {
        "no-skill", "base", "random-feedback", "greybox-feedback",
        "skillrace-feedback", "expert",
    }
    assert all(value["repeats"] == 3 for value in manifest["evaluations"].values())
```

- [ ] **Step 2: Implement three explicit orchestration phases**

`python -m skillrace.rq3 campaign` stages the public scenario and runs the three headline methods from the same base-skill hash under the same protocol and total budget. `python -m skillrace.rq3 revise` builds three envelopes and revisions. `python -m skillrace.rq3 evaluate` is the first phase allowed to resolve `tests/` and `expert_skill/`; it runs six conditions with three repeats through the shared bounded resource pool.

The top-level output is:

```text
out/rq3/<protocol-id>/rep-<NNN>/<scenario>/
  public-stage/
  campaigns/{random,greybox,skillrace}/
  feedback/{random,greybox,skillrace}.json
  revisions/{random,greybox,skillrace}/
  evaluations/{no-skill,base,random-feedback,greybox-feedback,skillrace-feedback,expert}/
  rq3-manifest.json
```

The manifest links protocol hash, public-stage hash, base generation/skill hashes, campaign allocation and records, feedback hashes, revision hashes, hidden-test hashes, expert-skill hash, every run ID, result hashes, model configuration, cost, and timestamps. Write it atomically and support resume by verifying every existing hash before skipping a phase.

- [ ] **Step 3: Add process-level leakage assertions**

Campaign and revision subprocess launch records must prove their current directory is under `public-stage`, their argument list contains no original scenario/test path, and their environment contains no variable whose value resolves under `scenarios/<name>/tests`. A test injects a sentinel phrase into a temporary hidden prompt and asserts that phrase appears in no campaign case, envelope, revision request, or revised skill.

- [ ] **Step 4: Run and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_rq3_manifest.py tests/test_rq3_leakage.py -q
.venv/bin/python -m skillrace.rq3 --help
```

Expected: linkage and leakage tests pass; CLI exposes three phases and `all`.

```bash
git add skillrace/rq3.py tests/test_rq3_manifest.py tests/test_rq3_leakage.py
git commit -m "feat: orchestrate linked RQ3 experiment"
```

### Task 6: Compute paired RQ3 outcomes without pooling away scenarios

**Files:**
- Create: `skillrace/analyze_rq3.py`
- Create: `tests/test_analyze_rq3.py`

- [ ] **Step 1: Write paired-delta tests**

Create fixture results for two tests where base has pass probabilities `1/3` and `2/3`, and SkillRACE-feedback has `2/3` and `2/3`. Assert the per-test deltas are `1/3` and zero and the mean delta is `1/6`. Assert stable 3/3 and strict metrics are separate fields.

- [ ] **Step 2: Implement analysis records**

Emit one row per scenario, campaign replication, hidden test, and condition with successes, trials, functional probability, strict probability, stable indicator, tokens, cost, and wall time. Compute each revision's paired delta from the base condition on the same hidden test. Aggregate first within scenario/replication, then across scenarios; never treat 100 tests as independent scenarios. Expert and no-skill remain descriptive references and are not folded into feedback-method rankings.

For final uncertainty, resample scenarios with replacement and retain every hidden test, campaign replication, repeat, and paired condition within the sampled scenario. Use the frozen analysis RNG seed and report 95% intervals for each base-to-revision delta. Also report every scenario-level delta so a pooled improvement cannot hide regressions.

- [ ] **Step 3: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_analyze_rq3.py -q`

Expected: paired calculations pass.

```bash
git add skillrace/analyze_rq3.py tests/test_analyze_rq3.py
git commit -m "feat: analyze paired RQ3 improvements"
```

### Task 7: Perform one-scenario dry run before scaling

**Files:**
- Modify: `scenarios/README.md`
- Create: `experiments/rq3-dry-run.json`

- [ ] **Step 1: Freeze a dry-run configuration**

Use `json-csv`, one campaign replication, budget six, bootstrap two, all three methods, six evaluation conditions, and three repeats on two explicitly recorded development hidden tests. Mark this configuration `development_only: true`; its outcomes never enter the paper.

- [ ] **Step 2: Run the end-to-end dry run**

Run:

```bash
.venv/bin/python -m skillrace.rq3 all \
  --scenario scenarios/json-csv \
  --protocol experiments/protocols/pilot.json \
  --replication 1 \
  --out out/rq3-dry-run
```

Expected: three campaigns, three envelopes, three revisions, six evaluation conditions × two tests × three repeats, and one verified manifest.

- [ ] **Step 3: Verify leakage and result completeness**

Run:

```bash
.venv/bin/python -m skillrace.rq3 verify out/rq3-dry-run
.venv/bin/python -m skillrace.analyze_rq3 --root out/rq3-dry-run --out out/rq3-dry-run/summary.json
```

Expected: no hash/leakage/missing-run errors and a paired summary for all three revisions.

- [ ] **Step 4: Commit only configuration and documentation**

Generated `out/` data remains untracked; commit the dry-run configuration and reproducible commands.

```bash
git add experiments/rq3-dry-run.json scenarios/README.md
git commit -m "docs: add reproducible RQ3 dry run"
```

## Plan 5 completion gate

Do not scale RQ3 until the dry-run manifest verifies, every condition has the same hidden hashes and three repeats, revision prompts differ only by envelope content, campaign/revision phases prove hidden-path absence, and paired analysis reproduces from raw run records.
