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
  base_skill/SKILL.md    the ONE zero-shot LLM-generated skill all conditions start from
  tests/<tk>/
    candidate.json       {"skill": "<name>", "prompt": "...", "base_image": "skillrace/skillgen-base:latest"}
    Dockerfile           FROM the base; build this test's starting /workspace
    checks/*.sh          pass-criteria; bash, run in the FINAL container, exit 0 = pass
```

A test **passes** iff every one of its `checks/*.sh` exits 0 (fixed invariants from the
harness are reported separately, per `skillrace.skill_eval`). Checks run in the final
container, which exposes `/workspace` (the agent's result), `/check/trace.jsonl`, and
`/check/workspace.diff`, exactly as in the main property checker — so a check may assert
on the artifact, run it on an example it constructs, or read the trace.

## How a scenario is used

```bash
# 1. revise the base skill from a campaign's findings (feedback-only; never sees tests/)
python -m skillrace.revise_skill --skill-dir scenarios/<name>/base_skill \
    --feedback out/campaign/<method>/<name>/campaign.json --out candidates/<name>-<method>

# 2. evaluate any skill version on the hidden tests
python -m skillrace.skill_eval --scenario scenarios/<name> --skill-name <name> \
    --skill-dir scenarios/<name>/base_skill      --out out/skill-eval/<name>-zeroshot
python -m skillrace.skill_eval --scenario scenarios/<name> --skill-name <name> \
    --skill-dir candidates/<name>-skillrace      --out out/skill-eval/<name>-skillrace
```

## Base image

All tests use `base_image = skillrace/skillgen-base:latest`, which must provide `python3`,
`git`, and the `pi` agent harness, and mount the skill under evaluation at
`/skills/<name>` (the harness overlays it, per `skillrace.skill_eval`). Tests keep their
starting environments to the base toolchain (Python stdlib + `pytest`) so they build
fast and check without extra dependencies.

## Status (2026-07-05)

Ten scenarios, each with a target purpose, a zero-shot base skill, and **10 hidden tests**
= **100 tests, 192 executable checks total**. Checks are **facet-driven**: each test gets
as many checks as it has real correctness facets (rich scenarios like `argparse-cli`,
`config-parser`, `regex-validate` average ~2.6-3.0; pure-function scenarios like
`interval-merge`/`text-template` are ~1, since forcing more would be padding). Per-scenario
counts: regex 30, argparse/config/csv 26, fix-failing-test/sqlite 20, json-csv 12,
interval-merge/log-parser 11, text-template 10.

Three quality gates have been run and pass:
1. **Structure + syntax** — `scenarios/lint_checks.sh`: every test has a valid
   `candidate.json`, a `Dockerfile`, and ≥1 check; every check parses (`bash -n`).
2. **Satisfiability** — every check was run against a hand-written *correct* reference
   solution and accepts it (so no check is vacuously unsatisfiable), and, for
   `fix-failing-test`, the shipped bug genuinely fails while the reference fix passes.
3. **Build + in-container execution** — every environment builds against
   `skillrace/skillgen-base:latest` and produces the expected starting files; **all 192
   checks were run inside the built containers against hand-written reference solutions
   and pass** (per scenario: csv 26, regex 30, sqlite 20, json-csv 12, argparse 26,
   config 26, fix-failing-test 20, interval-merge 11, text-template 10, log-parser 11).
   This confirms no check is vacuously unsatisfiable and every expected value is correct.
   D2 is ready to drive a campaign; the only remaining step is running the agent under
   test on it (an experiment).

**Base image.** `skillrace/skillgen-base:latest` is derived offline from the existing
`skillrace/fix-failing-test:base` (python3 + pytest + git + the `pi` harness) with an
emptied `/workspace`; see the build recipe in `scenarios/build_base.sh`.

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