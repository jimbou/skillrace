# RQ3 Benchmark Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the ten existing skill-generation scenarios and 100 hidden tests into a leakage-safe, provenance-complete benchmark whose executable criteria have stored positive and negative oracle evidence.

**Architecture:** Each scenario gains the five approved artifact groups and a machine-readable manifest. Each hidden test gains a criterion contract, reference overlay, targeted mutants, integrity assets, and validation records. A dedicated audit command performs static checks offline and Docker-based reference/negative validation without invoking an agent or model.

**Tech Stack:** Python 3.12, pytest, Docker, Bash, JSON, SHA-256, existing scenario images.

---

## File map

- Create `skillrace/scenario_contract.py`: schemas, hashing, manifest loading, static validation.
- Create `skillrace/scenario_audit.py`: Docker reference/empty/mutant audit and evidence writer.
- Create `skillrace/generate_base_skill.py`: one-shot base-skill generation with complete provenance.
- Create `tests/test_scenario_contract.py`: repository-wide structural and leakage-boundary checks.
- Create `tests/test_scenario_oracles.py`: Docker-marked audit tests.
- Modify `skillrace/check_properties.py`: stage non-script oracle assets safely.
- Modify `scenarios/README.md` and `scenarios/lint_checks.sh`: delegate to the auditable validator.
- Modify all `scenarios/text-template/tests/*/candidate.json`: correct the public `{{key}}` contract.
- Modify `scenarios/json-csv/tests/t5/checks/no-crash.sh`: require success, output, and valid empty CSV.
- Modify error/timeout/performance checks identified below: prove the artifact exists and the command ran.
- Replace all `scenarios/fix-failing-test/tests/*/checks/tests-unedited.sh`: hash and harness integrity.
- Create under every scenario: `scenario.json`, `campaign/`, `expert_skill/`, `base_skill/generation.json`.
- Create under every hidden test: `test.json`, `oracle/reference/`, `oracle/mutants/`, `oracle/evidence/`, and check assets where needed.

### Task 1: Define machine-readable scenario and hidden-test contracts

**Files:**
- Create: `skillrace/scenario_contract.py`
- Create: `skillrace/scenario_audit.py`
- Create: `tests/test_scenario_contract.py`

- [ ] **Step 1: Write repository-wide failing contract tests**

```python
import pathlib

from skillrace.scenario_contract import load_scenario, load_test


SCENARIOS = (
    "argparse-cli", "config-parser", "csv-stats", "fix-failing-test",
    "interval-merge", "json-csv", "log-parser", "regex-validate",
    "sqlite-query", "text-template",
)


def test_exactly_ten_scenarios_with_ten_tests_each():
    assert tuple(sorted(path.name for path in pathlib.Path("scenarios").iterdir()
                        if (path / "scenario.md").exists())) == tuple(sorted(SCENARIOS))
    for name in SCENARIOS:
        tests = sorted((pathlib.Path("scenarios") / name / "tests").glob("t*"))
        assert len(tests) == 10


def test_every_scenario_has_five_disjoint_artifact_groups():
    for name in SCENARIOS:
        scenario = load_scenario(pathlib.Path("scenarios") / name)
        assert scenario.base_skill_dir.name == "base_skill"
        assert scenario.campaign_dir.name == "campaign"
        assert scenario.hidden_tests_dir.name == "tests"
        assert scenario.expert_skill_dir.name == "expert_skill"
        assert scenario.base_skill_dir not in scenario.hidden_tests_dir.parents


def test_every_hidden_test_has_criteria_reference_and_negative_oracles():
    for name in SCENARIOS:
        for path in sorted((pathlib.Path("scenarios") / name / "tests").glob("t*")):
            contract = load_test(path)
            assert contract.criteria
            assert contract.reference_overlay.is_dir()
            assert contract.mutants
            assert all(criterion.mutant_ids for criterion in contract.criteria)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_scenario_contract.py -q`

Expected: import or missing-artifact failures enumerate the incomplete packages.

- [ ] **Step 3: Implement strict loaders**

`scenario.json` schema:

```json
{
  "schema": "skillrace-scenario/1",
  "scenario_id": "json-csv",
  "purpose_sha256": "ed52a3ed794fba725a80e77ae9977e86584a5d54cf4771a4e4a3cbb5e3d15230",
  "base_skill": {"path": "base_skill", "generation_record": "base_skill/generation.json"},
  "campaign": {"path": "campaign", "properties": "campaign/properties.json", "generation": "campaign/generation.json"},
  "hidden_tests": {"path": "tests", "count": 10},
  "expert_skill": {"path": "expert_skill", "provenance": "expert_skill/provenance.json"},
  "contingency": "high"
}
```

Each `tests/tN/test.json` schema:

```json
{
  "schema": "skillrace-hidden-test/1",
  "test_id": "json-csv/t5",
  "candidate_sha256": "15f950fccb5581464cf5c66dd23b25e8649d8f2beca46871eafdec0727a3ca4b",
  "dockerfile_sha256": "f79c29bb0bec0b9f6eaabdc674abda14f03cd2f2187a05bb73ebec8a01b3547b",
  "entrypoint": "python3 convert.py in.json out.csv",
  "criteria": [
    {"id": "empty-array", "script": "checks/empty-array.sh", "kind": "functional", "mutant_ids": ["no-output", "invalid-csv"]}
  ],
  "reference_overlay": "oracle/reference",
  "mutants": [
    {"id": "no-output", "overlay": "oracle/mutants/no-output"},
    {"id": "invalid-csv", "overlay": "oracle/mutants/invalid-csv"}
  ]
}
```

Loaders reject absolute paths, `..`, duplicate IDs, missing files, hash mismatches, criteria without negative mutants, and any path escaping its scenario/test root. Dataclasses expose resolved paths but serialized manifests retain relative paths. Add CLI subcommands `validate ROOT`, `validate --test TEST_DIR`, and `refresh-hashes ROOT`; refresh changes only hash fields and prints every changed file for review.

- [ ] **Step 4: Add all scenario/test manifests**

Create ten `scenario.json` files and 100 `test.json` files. Criterion IDs equal script stems. Assign kinds from `functional`, `error`, `integrity`, and `performance`. Every criterion lists at least one mutant it must reject; a mutant may serve several criteria.

- [ ] **Step 5: Add the audit command before changing individual oracles**

Implement `python -m skillrace.scenario_audit --test TEST_DIR`, `--scenario SCENARIO_DIR`, and `--root scenarios`. For each selected test it builds the starting image, creates a derived container for each overlay, copies the checks directory to `/check/oracle`, runs every declared script, and returns a structured matrix. A reference is valid only when every criterion exits zero. An empty implementation is rejected when at least one criterion exits nonzero. For each mutant, every criterion listing its ID must exit nonzero. Always remove containers and derived images in `finally`; `--persist` atomically writes `oracle/evidence/validation.json`, while the default only prints JSON.

Expose a pure `grade_oracle_matrix(reference, empty, mutants, criteria)` helper so offline tests can validate the all/any rules without Docker.

- [ ] **Step 6: Run static tests and commit**

Run: `.venv/bin/python -m pytest tests/test_scenario_contract.py -q`

Expected: the count, package, and criterion-contract tests pass.

```bash
git add skillrace/scenario_contract.py skillrace/scenario_audit.py tests/test_scenario_contract.py scenarios/*/scenario.json scenarios/*/tests/*/test.json
git commit -m "feat: define auditable RQ3 scenario contracts"
```

### Task 2: Correct the text-template specification in all ten prompts

**Files:**
- Modify: `scenarios/text-template/tests/t1/candidate.json` through `t10/candidate.json`
- Extend: `tests/test_scenario_contract.py`

- [ ] **Step 1: Add the prompt consistency test**

```python
import json
import pathlib


def test_text_template_prompts_define_double_brace_placeholders():
    for path in pathlib.Path("scenarios/text-template/tests").glob("t*/candidate.json"):
        prompt = json.loads(path.read_text())["prompt"]
        assert "Replace every {{key}}" in prompt
        assert "Single braces like {x} are left untouched" in prompt
        assert "Replace every {key}" not in prompt
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_scenario_contract.py::test_text_template_prompts_define_double_brace_placeholders -q`

Expected: all ten current prompts fail the double-brace assertion.

- [ ] **Step 3: Apply the same corrected contract to all ten JSON files**

The shared prompt prefix must be exactly:

```text
Create /workspace/render.py invoked as `python3 render.py <template> <data.json> <out>`. Replace every {{key}} placeholder in the template with data[key]. A key absent from the data leaves the double-brace placeholder text EXACTLY as written. Single braces like {x} are left untouched.
```

Retain each test's final scenario-specific sentence. Recompute `candidate_sha256` in each `test.json` with `skillrace.scenario_contract refresh-hashes scenarios/text-template`, review the diff, and do not alter Dockerfiles or expected output.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/python -m pytest tests/test_scenario_contract.py -q`

Expected: prompt and manifest hashes pass.

```bash
git add scenarios/text-template tests/test_scenario_contract.py
git commit -m "fix: specify double-brace template placeholders"
```

### Task 3: Strengthen `json-csv/t5` from no-traceback to real behavior

**Files:**
- Replace: `scenarios/json-csv/tests/t5/checks/no-crash.sh`
- Modify: `scenarios/json-csv/tests/t5/test.json`
- Create: `scenarios/json-csv/tests/t5/oracle/mutants/no-output/convert.py`
- Create: `scenarios/json-csv/tests/t5/oracle/mutants/invalid-csv/convert.py`

- [ ] **Step 1: Replace the check with an executable condition**

```bash
#!/usr/bin/env bash
set -u
cd /workspace
[ -f convert.py ] || { echo 'FAIL convert.py missing'; exit 1; }
rm -f out.csv
python3 convert.py in.json out.csv >/tmp/json-csv-t5.out 2>&1
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL converter exit=$rc"; cat /tmp/json-csv-t5.out; exit 1; }
[ -f out.csv ] || { echo 'FAIL out.csv missing'; exit 1; }
python3 - <<'PY'
import csv
with open('out.csv', newline='', encoding='utf-8') as stream:
    rows = list(csv.reader(stream))
assert rows in ([], [[]]), rows
print('ok')
PY
```

Rename its criterion ID to `empty-array-valid-csv` while retaining the script filename for artifact compatibility. The `no-output` mutant exits zero without creating a file. The `invalid-csv` mutant writes an unmatched quote. Both must be rejected.

- [ ] **Step 2: Run syntax and negative-oracle checks**

Run:

```bash
bash -n scenarios/json-csv/tests/t5/checks/no-crash.sh
.venv/bin/python -m skillrace.scenario_audit --test scenarios/json-csv/tests/t5
```

Expected: reference passes; empty implementation and both mutants fail.

- [ ] **Step 3: Commit**

```bash
git add scenarios/json-csv/tests/t5
git commit -m "fix: make empty JSON-to-CSV check behavioral"
```

### Task 4: Harden error, timeout, and performance criteria

**Files:**
- Modify: negative-argument checks under `scenarios/argparse-cli/tests/*/checks/`
- Modify: error checks under `scenarios/config-parser/tests/*/checks/`
- Modify: `scenarios/csv-stats/tests/t8/checks/no-crash-on-mean.sh`
- Modify: `scenarios/csv-stats/tests/t9/checks/fast.sh`
- Modify: `scenarios/csv-stats/tests/t10/checks/no-crash.sh`
- Modify: all `scenarios/sqlite-query/tests/*/checks/clean.sh`
- Extend: `skillrace/scenario_contract.py`

- [ ] **Step 1: Add static semantic rules for special criterion kinds**

For every `error` criterion, require the script source to contain an explicit implementation-file existence check, invoke the declared entrypoint, and capture `rc=$?`. For every `performance` criterion, require `timeout`, an explicit zero return-code assertion, and a functional output assertion. Static rules are guardrails; Docker mutant runs remain the actual oracle validation.

- [ ] **Step 2: Harden each listed script**

Use this error-check structure, substituting the declared artifact and arguments:

```bash
[ -f loadcfg.py ] || { echo 'FAIL loadcfg.py missing'; exit 1; }
python3 loadcfg.py broken.ini >/tmp/check.out 2>&1
rc=$?
[ "$rc" -ne 0 ] || { echo 'FAIL invalid input accepted'; exit 1; }
grep -qi traceback /tmp/check.out && { echo 'FAIL leaked traceback'; exit 1; }
grep -qi port /tmp/check.out || { echo 'FAIL error did not name port'; exit 1; }
echo ok
```

Use this performance structure for `csv-stats/t9`:

```bash
#!/usr/bin/env bash
set -u
cd /workspace
[ -f stats.py ] || { echo 'FAIL stats.py missing'; exit 1; }
timeout 10 python3 stats.py sum --column n --file big.csv >/tmp/stats.out 2>&1
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL command exit=$rc"; exit 1; }
value=$(tr -d '[:space:]' </tmp/stats.out)
case "$value" in 5050|5050.0) echo ok;; *) echo "FAIL output=$value"; exit 1;; esac
```

For every SQLite `clean.sh`, require `answer.py`, capture and require exit zero, then reject `error|traceback` output. For CSV no-crash checks, require the artifact and an explicitly permitted return-code policy justified in `test.json`.

- [ ] **Step 3: Add missing-command and wrong-return-code mutants**

Every special-kind criterion receives a mutant that omits the artifact and one that produces the superficially expected message with the wrong exit status or wrong output. Update `mutant_ids` accordingly.

- [ ] **Step 4: Run static validation and commit**

Run:

```bash
.venv/bin/python -m skillrace.scenario_contract validate scenarios
bash scenarios/lint_checks.sh
```

Expected: 10 scenarios, 100 tests, and all special-kind semantic rules pass.

```bash
git add skillrace/scenario_contract.py scenarios/argparse-cli scenarios/config-parser scenarios/csv-stats scenarios/sqlite-query
git commit -m "fix: make error and performance checks executable"
```

### Task 5: Detect every fix-failing-test integrity bypass

**Files:**
- Modify: `skillrace/check_properties.py:204-235`
- Create: `scenarios/fix-failing-test/tests/*/checks/assets/integrity_check.py`
- Replace: `scenarios/fix-failing-test/tests/*/checks/tests-unedited.sh`
- Create: `scenarios/fix-failing-test/tests/*/checks/assets/initial-integrity.json`
- Create: targeted mutants for edit, delete, rename, skip, harness override, and assertion weakening

- [ ] **Step 1: Stage check assets, not only shell scripts**

Before executing precompiled/hidden checks, copy the complete checks directory into the live container at `/check/oracle` using `docker cp CHECKS_DIR/. CONTAINER:/check/oracle/`. Reject symlinks and any resolved source path outside the checks directory. Execute scripts from `/check/oracle/<script>`.

- [ ] **Step 2: Add the deterministic integrity checker**

```python
import hashlib
import json
import pathlib
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


root = pathlib.Path("/workspace")
expected = json.loads(pathlib.Path("/check/oracle/assets/initial-integrity.json").read_text())
errors = []
for relative, expected_hash in expected["protected_files"].items():
    path = root / relative
    if not path.is_file():
        errors.append(f"missing or renamed: {relative}")
    elif digest(path) != expected_hash:
        errors.append(f"content changed: {relative}")

command = ["git", "diff", "--cached", "--name-status", "HEAD", "--"] + expected["protected_globs"]
changed = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False).stdout.strip()
if changed:
    errors.append("protected test/harness paths changed: " + changed.replace("\n", "; "))

if errors:
    print("FAIL " + " | ".join(errors))
    raise SystemExit(1)
print("ok")
```

Each asset records the exact SHA-256 of the starting test file and protected globs for test files plus `conftest.py`, `pytest.ini`, `pyproject.toml`, `setup.cfg`, and `tox.ini`. Store the checker beside that manifest as `checks/assets/integrity_check.py`. Each `tests-unedited.sh` becomes:

```bash
#!/usr/bin/env bash
set -u
python3 /check/oracle/assets/integrity_check.py
```

- [ ] **Step 3: Add and run six bypass mutants**

For every test, store at least one direct edit mutant. Across the ten-test scenario, cover deletion, rename, `pytest.skip`, assertion weakening, a `conftest.py` collection override, and a `pytest.ini` ignore override. The integrity criterion must reject all six classes before the suite-pass criterion is considered.

- [ ] **Step 4: Run the scenario audit and commit**

Run: `.venv/bin/python -m skillrace.scenario_audit --scenario scenarios/fix-failing-test`

Expected: ten references pass, every shipped bug fails before reference overlay, and all integrity mutants are rejected.

```bash
git add skillrace/check_properties.py scenarios/fix-failing-test
git commit -m "fix: detect hidden-test integrity bypasses"
```

### Task 6: Store reference overlays, targeted mutants, and validation evidence

**Files:**
- Create: `skillrace/scenario_audit.py`
- Create: `tests/test_scenario_oracles.py`
- Create: `scenarios/*/tests/*/oracle/`

- [ ] **Step 1: Implement the audit lifecycle**

For each test, the audit command:

1. builds its Dockerfile and records the image ID/digest;
2. runs the unmodified starting state and records which criteria fail;
3. overlays `oracle/reference/`, runs every criterion, and requires all to pass;
4. runs an empty implementation and requires the test as a whole to fail when applicable;
5. overlays every mutant and requires every criterion naming that mutant to fail;
6. stores command, exit code, final output line, duration, script hash, overlay hash, and image digest under `oracle/evidence/validation.json`;
7. removes all audit containers/images in `finally` blocks.

No model, agent, base skill, revised skill, or campaign output is involved.

- [ ] **Step 2: Add complete reference implementations and mutants**

Store a reference overlay for all 100 tests. Scenario-level APIs remain consistent: `tool.py`, `loadcfg.py`, `stats.py`, the relevant implementation module for fix-failing-test, `merge_intervals.py`, `convert.py`, `logstat.py`, `validator.py`, `answer.py`, and `render.py`. Mutants are minimal and named for the fault they introduce, such as `split-on-comma`, `unanchored-regex`, `first-object-header`, `substring-log-level`, `touching-not-merged`, and `missing-key-none`.

- [ ] **Step 3: Add the Docker-marked gate**

```python
import pathlib

import pytest

from skillrace.scenario_audit import audit_test


@pytest.mark.docker
@pytest.mark.parametrize("test_dir", sorted(pathlib.Path("scenarios").glob("*/tests/t*")))
def test_reference_and_negative_oracles(test_dir):
    report = audit_test(test_dir, persist=False)
    assert report["reference_passed"] is True
    assert report["negative_oracles_passed"] is True
```

- [ ] **Step 4: Run the full audit**

Run:

```bash
.venv/bin/python -m pytest -m docker tests/test_scenario_oracles.py -q
.venv/bin/python -m skillrace.scenario_audit --root scenarios --persist
```

Expected: 100 references pass; every required empty/incorrect implementation is rejected; every criterion kills each assigned mutant; 100 validation records are written.

- [ ] **Step 5: Commit**

```bash
git add skillrace/scenario_audit.py tests/test_scenario_oracles.py scenarios/*/tests/*/oracle
git commit -m "test: store positive and negative oracle evidence"
```

### Task 7: Complete the five scenario artifact groups and provenance

**Files:**
- Create: `skillrace/generate_base_skill.py`
- Create: `scenarios/*/campaign/properties.json`
- Create: `scenarios/*/campaign/generation.json`
- Create: `scenarios/*/expert_skill/SKILL.md`
- Create: `scenarios/*/expert_skill/provenance.json`
- Create/replace: `scenarios/*/base_skill/SKILL.md`
- Create: `scenarios/*/base_skill/generation.json`

- [ ] **Step 1: Implement one-shot base-skill generation**

The command reads only the `Target purpose` paragraph from `scenario.md`—not the revision rubric, campaign properties, expert skill, or hidden tests—uses the frozen shared model at temperature zero, and writes `base_skill/SKILL.md`. Store the exact extracted text as `base_skill/generation-input.txt`. Record exact system/user prompts, raw response, model, temperature, reasoning setting, output limit, UTC timestamp, API usage/cost, full scenario hash, generation-input hash, response hash, and final skill hash. It refuses to overwrite an existing generation record without `--regenerate`, which archives the previous pair by hash.

- [ ] **Step 2: Regenerate all ten unprovenanced base skills**

The current base skills have no recoverable model provenance, so generate them once under the same selected model. Do not edit the generated text after creation. If a generated skill is malformed, record the malformed generation and rerun the entire predeclared generation procedure with a new replication ID; never hand-repair it.

- [ ] **Step 3: Add campaign-visible material**

Each `campaign/properties.json` contains must-hold properties derived from the public purpose, not hidden test details. Each `campaign/generation.json` fixes allowed runtimes/tools, base image digest, proposer/realizer prompt versions, sanity predicates, and environment boundaries. Tests assert neither file contains a hidden prompt substring or a `tests/` path.

- [ ] **Step 4: Add independently authored expert skills**

Write ten concise expert skills from `scenario.md` only. Each provenance record identifies the human authoring procedure, source purpose hash, UTC date, and confirms that hidden tests were not consulted. The expert condition is an upper bound reported separately, never training data for revisions.

- [ ] **Step 5: Validate and commit**

Run:

```bash
.venv/bin/python -m skillrace.scenario_contract validate scenarios
.venv/bin/python -m pytest tests/test_scenario_contract.py -q
```

Expected: all five groups and hashes validate for all ten scenarios.

```bash
git add skillrace/generate_base_skill.py scenarios/*/campaign scenarios/*/expert_skill scenarios/*/base_skill
git commit -m "feat: complete RQ3 scenario packages"
```

### Task 8: Replace the legacy lint script and document the real evidence

**Files:**
- Modify: `scenarios/lint_checks.sh`
- Modify: `scenarios/README.md`
- Modify: `docs/implementation-status.md`

- [ ] **Step 1: Delegate linting to the contract validator**

`scenarios/lint_checks.sh` must run `.venv/bin/python -m skillrace.scenario_contract validate scenarios` and exit with its status. Keep `bash -n` inside the Python validator so one command covers schema, hashes, paths, criteria, and shell syntax.

- [ ] **Step 2: Correct status claims**

Replace historical claims that references were validated but not stored with links to the committed `oracle/evidence/validation.json` records. Document the distinction among static validation, positive satisfiability, empty/incorrect rejection, targeted mutation strength, and live agent evaluation.

- [ ] **Step 3: Run and commit**

Run:

```bash
bash scenarios/lint_checks.sh
.venv/bin/python -m pytest tests/test_scenario_contract.py -q
git diff --check
```

Expected: all commands pass.

```bash
git add scenarios/lint_checks.sh scenarios/README.md docs/implementation-status.md
git commit -m "docs: link RQ3 oracle validation evidence"
```

## Plan 4 completion gate

The benchmark is ready only when the static gate passes, all 100 Docker reference audits pass, all assigned mutants are rejected, base-skill provenance exists for every scenario, and no public/campaign artifact contains a hidden-test path or content. Commit the resulting validation evidence before any RQ3 agent run.
