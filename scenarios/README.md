# D2 — skill-generation scenarios (hidden-test suite)

These scenarios drive RQ3: *does revising an LLM-generated skill with \tool's findings
raise its pass rate on held-out tests, versus revising with the baselines' findings?*

## Leakage rule (the thing that makes RQ3 valid)

> The hidden tests and their checks in `tests/` are authored **independently of every
> revision loop** and are **never shown** to any tester (\tool, greybox, floor) or to
> the reviser. See [`docs/dataset-protocol.md`](../docs/dataset-protocol.md) §5.

The revision loop for a scenario is only ever given the scenario's *target purpose* and
whatever a tester generates from it; it never sees `tests/`. The reviser prompt is
byte-identical across the four conditions (zero-shot / floor / greybox / \tool); only
the feedback payload differs. So a held-out pass-rate difference is attributable to
feedback quality, not to leakage or a better reviser.

## Directory format

```
scenarios/<name>/
  scenario.md            target skill purpose + what a good SKILL.md must teach + rubric
  base_skill/SKILL.md    the ONE zero-shot skill all conditions start from
  base_skill/.skillrace/ complete model-call provenance (required before a headline run)
  campaign/              public properties, applicability, and base-image configuration
  tests/<tk>/
    candidate.json       {"skill": "<name>", "prompt": "...", "base_image": "skillrace/skillgen-base:0.73.1-construction"}
    Dockerfile           FROM the base; build this test's starting /workspace
    checks/*.sh          pass-criteria; bash, run in the FINAL container, exit 0 = pass
```

A test **passes** iff every one of its `checks/*.sh` exits 0 (fixed invariants from the
harness are reported separately, per `skillrace.skill_eval`). The finished filesystem
is snapshotted once and every check runs in a fresh isolated child exposing `/workspace`
(the agent's result), `/check/trace.jsonl`, and `/check/workspace.diff`, exactly as in
the main property checker. A check may assert on the artifact, run it on an example it
constructs, or read the trace, but it cannot leave state that helps a later check.

## How a scenario is used

```bash
python -m skillrace.rq3_pipeline run \
  --scenario scenarios/<name> \
  --scenarios-root scenarios \
  --protocol experiments/protocols/issta-main.json \
  --out out/rq3/<name>

python -m skillrace.rq3_pipeline verify \
  --scenario scenarios/<name> --out out/rq3/<name>
```

The orchestrator stages only `scenario.md`, `base_skill/`, and `campaign/`; runs the
three 30-execution campaigns; confirms one representative per deduplicated
property/failure signature outside that budget; creates equal byte-bounded feedback;
revises blindly; then opens `tests/` only for the four-condition hidden evaluation.

## Base image

All frozen test templates use
`base_image = skillrace/skillgen-base:0.73.1-construction`, which provides the locked
Python/pytest environment and Pi 0.73.1. At execution time, `skillrace.skill_eval`
deterministically projects only that base reference to the selected model-track image
(`...:0.73.1-glm-4.5-flash` or `...:0.73.1-deepseek-v4-flash`) and records both source
and projected hashes. The skill under evaluation remains a trusted read-only host mount.

## Validation status (2026-07-12)

Ten scenarios, each with a target purpose, a zero-shot base skill, and **10 hidden tests**
= **100 tests, 192 executable checks total**. Checks are **facet-driven**: each test gets
as many checks as it has real correctness facets (rich scenarios like `argparse-cli`,
`config-parser`, `regex-validate` average ~2.6-3.0; pure-function scenarios like
`interval-merge`/`text-template` are ~1, since forcing more would be padding). Per-scenario
counts: regex 30, argparse/config/csv 26, fix-failing-test/sqlite 20, json-csv 12,
interval-merge/log-parser 11, text-template 10.

The original checked-in base-skill model calls did not retain recoverable provider
provenance. Each package is therefore truthfully marked `regeneration-required`; the
headline orchestrator fails closed until it is regenerated through
`skillrace.rq3_base` and carries prompt, response, model, token, cost, and hash records.
These markers are not presented as completed generation provenance.

The reviewable package is complete: ten `scenario.json` contracts, 100
`test.json` contracts, 100 reference overlays, criterion-assigned negative overlays,
and 100 validated Docker evidence records. `bash scenarios/lint_checks.sh` checks the
exact scenario/test counts, IDs, candidate/Docker/check hashes, JSON and Bash syntax,
safe paths, content identities, public/hidden boundary, oracle layout, and the strict
static execution patterns for all 192 checks.

Runtime oracle evidence was explicitly reset before the Pi 0.73.1 template migration,
then regenerated and persisted on 2026-07-12 against the replacement locked construction
base. Every criterion was executed in a fresh container, matching the production
checker's non-contamination boundary. All 100 reference implementations pass, all 100
starting states are rejected, and all 215 assigned negative-criterion pairs are killed. Each
`oracle/evidence/validation.json` records the contract identity, built image digest,
Docker version, commands, return codes, script hashes, isolation mode, timings, and
negative results.
The root validator reports `pending_docker=0`, `audit_failed=0`, and
`runtime_ready=true`.

To reproduce or refresh the Docker gate:

```bash
SKILLRACE_RUN_DOCKER=1 python3 -m pytest -m docker tests/test_scenario_oracles.py
python3 -m skillrace.scenario_audit --root scenarios --persist
python3 -m skillrace.scenario_contract validate scenarios --require-runtime-evidence
```

The final command must return zero before an RQ3 result is admitted. Changing the
shared base, a candidate, Dockerfile, check, reference, negative overlay, or contract
invalidates the bound evidence and requires this refresh.

**Base image.** `skillrace/skillgen-base:0.73.1-construction` combines the locked
source-built Python 3.11.2/pytest 7.2.1 bootstrap with Pi 0.73.1. Two tiny final overlays
bake one exact Yunwu model catalog each. The authoritative sources and immutable IDs are
in `images/skillgen-base/` and `experiments/image-locks/`.

| Scenario | Task family | Contingency | Tests / checks |
|----------|-------------|:-----------:|:--------------:|
| `csv-stats`       | CLI: stats over CSV            | high | 10 / 26 checks |
| `json-csv`        | convert JSON ↔ CSV to spec     | high | 10 / 12 checks |
| `sqlite-query`    | answer questions over SQLite   | high | 10 / 20 checks |
| `regex-validate`  | implement validators to spec   | med  | 10 / 30 checks |
| `argparse-cli`    | CLI w/ subcommands + exit codes| high | 10 / 26 checks |
| `config-parser`   | parse + validate a config      | high | 10 / 26 checks |
| `fix-failing-test`| fix impl so the suite passes   | high | 10 / 20 checks |
| `interval-merge`  | implement a spec'd algorithm   | med  | 10 / 11 checks |
| `text-template`   | render a template to spec      | med  | 10 / 10 checks |
| `log-parser`      | parse + aggregate log lines    | high | 10 / 11 checks |
