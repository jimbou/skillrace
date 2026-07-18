# SkillRACE Next Handoff

Date: 2026-07-18

## Bottom line

The planned single-campaign clean-room implementation is complete. Tasks 1–16 are
implemented, tested, live-verified, documented, and committed. `skillrace_next` can be
used now with input skills under `skills/` and input scenarios under `scenarios/` by
running:

```text
python -m skillrace_next ...
```

No package rename is required to run experiments. The final legacy-package cutover was
not authorized and was not performed.

What remains is primarily experiment preparation and execution. One technical gap must
also be resolved before treating `replicate_count > 1` as implemented: the production
CLI currently runs exactly one campaign per invocation.

## Ready now

- Part I accepts an immutable existing S0, its provenance receipt, a skill ID, and an
  ordered property list.
- Part II generates one S0 from a public scenario, gives every method an identical copy,
  and lets Random, VeriGrey, and SkillRACE generate their own development tests.
- Part II opens held-out tests only after every method has completed development.
- A patch is admitted only when at least one previously failing check becomes passing,
  no previously passing check becomes failing, and all retained-test checks pass.
- Invalid replacement proposals are recorded as missed slots without spending a weak
  agent run.
- Every non-verifier role in a track uses the configured cheap model.
- Codex `gpt-5.6-terra` with medium reasoning authors checks only. It does not patch and
  receives neither provider credentials nor Docker access.
- Same-track Pi performs patching from immutable evidence.
- Checker scripts execute through `docker exec`; their JSON output is authoritative.
- Weak-agent timeout is an experimental outcome. Its partial artifact is checked without
  retrying the weak agent for luck.

## Required next work for full experiments

The completed live gates are bounded contracts. They prove the pipeline boundaries but
are not a full scientific run over the repository's skills and scenarios.

### 0. Resolve replicate and matrix execution

One `part1` or `part2` invocation currently executes one skill/scenario/model campaign.
`ExperimentConfig.replicate_count` is validated and frozen for provenance, but the CLI
composition does not loop over it. There is also no multi-skill, multi-scenario, or
multi-model matrix runner in `skillrace_next`.

Do not set `replicate_count` above `1` and assume multiple independent campaigns were
executed. Until this is implemented, the scientifically safe operational workaround is:

- set `replicate_count` to `1`;
- issue one explicit CLI invocation per replicate/cell;
- give every invocation a unique `experiment_id` and `output_root`; and
- aggregate only after verifying every expected cell exists.

If code support is desired, implement the smallest direct outer sequential loop over
replicates, with a focused failing test proving distinct output directories and S0/input
identity. Do not add a scheduler, matrix engine, workflow framework, recovery system, or
parallel campaign manager. Independent-cell parallelism can be considered only after the
sequential outer loop is correct.

### 1. Choose the real study inputs

The next operator must select, without guessing:

- which existing skills are Part I S0 inputs;
- which public scenarios are Part II inputs;
- which tests are held out until final evaluation;
- iteration budgets, held-out repetitions, and the explicit replicate/cell schedule; and
- which model tracks are included in the actual study.

The implemented final-development tracks are:

- `lab/deepseek-v4-flash`; and
- `lab/qwen3.6-flash`.

`yunwu/deepseek-v3.2` remains supported. Within a track, do not mix cheap models across
proposer, weak-agent, segmenter, alignment, generator, or patcher roles.

### 2. Prepare Part I inputs

Each selected Part I skill needs:

```text
skills/<skill>/SKILL.md
skills/<skill>/<provenance-receipt>.json
scenarios/<scenario>/properties.json
```

`properties.json` must be an ordered, nonempty list of unique property IDs and
descriptions. The Part I CLI does not invent S0 provenance or property definitions.

Example invocation:

```bash
python -m skillrace_next part1 \
  --config path/to/part1-config.json \
  --s0-dir skills/my-skill \
  --s0-receipt skills/my-skill/receipt.json \
  --skill-id my-skill \
  --properties scenarios/my-scenario/properties.json \
  --live
```

### 3. Prepare Part II held-out records

Part II needs one nonempty public scenario file and at least one held-out test record:

```text
scenarios/<scenario>/scenario.md
scenarios/<scenario>/heldout/<test>/test-case.json
```

Each `test-case.json` must use schema `skillrace-test-case/1` and bind the exact prompt,
Docker environment, NL checks, proposal receipt, and their hashes. Relative paths are
resolved against the record's directory.

Many existing repository scenarios use older scenario-specific `test.json` files. Those
files are not automatically compatible. Reuse their prompt, environment, NL-check, and
receipt assets, but create a strict `TestCase` record beside them. Do not add a schema
migration or compatibility layer.

The existing `skills/` and `scenarios/` trees were not modified during the clean-room
work. They are outside the prior write scope and contain substantial unrelated dirty
changes. Editing or converting them requires a new explicit instruction and careful,
file-specific commits.

Example invocation:

```bash
python -m skillrace_next part2 \
  --config path/to/part2-config.json \
  --scenario scenarios/my-scenario/scenario.md \
  --heldout-test scenarios/my-scenario/heldout/t1/test-case.json \
  --heldout-test scenarios/my-scenario/heldout/t2/test-case.json \
  --live
```

### 4. Create real experiment configs

For repository-backed inputs, use separate input and output roots:

```json
{
  "suite_path": "scenarios/my-scenario",
  "scenario_path": "scenarios/my-scenario/scenario.md",
  "output_root": "out/my-experiment"
}
```

The complete strict config has additional required fields documented in
[Configuration, Providers, and CLI](CONFIGURATION_AND_CLI.md). In Part II, keep the
frozen `scenario_path` equal to the explicit `--scenario` argument.

Three config/CLI consistency rules are currently operator-enforced rather than checked
by the command:

- set `replicate_count` to `1`, because one invocation runs one campaign;
- set config `live` to `true` for a paid run and also pass `--live`; and
- in Part II, pass the same path in config `scenario_path` and `--scenario`.

The CLI gate still prevents paid work unless `--live` is present. The gap is that it does
not reject a frozen config whose `live` value disagrees with that flag, and it does not
reject a Part II scenario argument that disagrees with the frozen provenance path. Before
a full study, either enforce these two equalities with focused tests or validate them in
the run-launch script and inspect every frozen config. Prefer direct rejection of a
mismatch; do not silently rewrite the frozen scientific inputs.

Before spending provider budget, freeze each config without `--live`:

```bash
python -m skillrace_next part1 --config path/to/part1-config.json
python -m skillrace_next part2 --config path/to/part2-config.json
```

Use a fresh `output_root` for each actual run. Do not reuse or overwrite a completed run
directory.

### 5. Run and inspect the actual experiments

Paid commands require explicit `--live`. For each completed run:

- inspect the first generated test from every method;
- inspect generated S0 and every accepted patch semantically;
- verify accepted transitions against before/replay/regression results;
- verify held-out files were first opened only after all development loops;
- scan evidence for the exact active provider credential without printing it;
- verify checker execution receipts and artifact immutability; and
- verify no SkillRACE-owned container remains.

Do not rerun an incorrect artifact, timeout, rejected patch, or unfavorable method result
to obtain a better outcome. Stop on persistent provider failure and preserve the failed
receipt.

## Optional later work

These items are not blockers for using `skillrace_next` now.

### Legacy package cutover

Cutover would rename or install `skillrace_next` as `skillrace`, move/archive the old
implementation, and update canonical imports and entry points. This requires explicit
approval. It should be a separate task with fresh tests and a carefully scoped commit.

### Runtime image naming

The pinned Pi runtime image tag still contains the historical `deepseek-v3.2` label even
when its mounted model catalog selects a Lab model. Receipts preserve the real image ID
and model identity, so behavior is correct. Rename/rebuild only if clearer artifact
naming is worth invalidating the existing image-name references; preserve the old image
hash evidence.

### Thin `analyze` command

`python -m skillrace_next analyze` copies an existing run summary into `analysis.json`.
It does not verify or aggregate multiple experiment cells. A full multi-replicate study
therefore needs either an explicit external aggregation step or a newly specified direct
aggregator. Add it only when the exact required report and expected cell set have been
settled.

### Large modules

`pipeline/stages.py`, `pipeline/campaigns.py`, `methods/skillrace.py`, and `runtime/pi.py`
are large. Do not split them solely by line count. The two pipeline loops remain direct
and sequential; extract code only if a future concrete change creates a clear boundary.

## Environment and safety notes

- The repository worktree contains extensive unrelated dirty and untracked legacy work.
  Do not reset, clean, reformat, or include it in commits.
- Continue writing pipeline changes only under `skillrace_next/` and `tests_next/` unless
  the user explicitly expands scope.
- In this environment, noninteractive shells may return early from `.bashrc`. Paid runs
  successfully loaded the rotated Lab credential through an interactive shell such as
  `bash -ic '...'`. Never print the credential.
- Use `deepseek-v4-flash` for incremental Lab development unless the study configuration
  explicitly selects another supported track. Use both Lab tracks only for a deliberately
  bounded dual-model gate or the actual configured comparison.
- The final verification ended with no running Docker containers and clean exact-key
  scans.

## Verification already completed

- 155 unit/integration tests passed.
- Separate real production Part I and Part II CLI contracts passed.
- Individual live contracts passed for provider/Pi, task execution, proposal, Codex
  verifier, Docker checker execution, episode creation, tree merge/alignment, SkillRACE
  proposal, VeriGrey proposal, S0 generation, patching, and exact replay.
- The final DeepSeek/Qwen gate passed `2` parameterized cases in `25m30s`.

Evidence:

```text
out/live-contracts/cli-part1/deepseek-v4-flash/20260718T012237Z-997c25ac/
out/live-contracts/cli-part2/deepseek-v4-flash/20260718T014531Z-1d582fa0/
out/live-contracts/dual-model-gate/deepseek-v4-flash/20260718T021119Z-de8da6fc/
out/live-contracts/dual-model-gate/qwen3.6-flash/20260718T022206Z-49824891/
```

Key commits:

```text
38cc6a6 feat(next): evolve skills through cumulative iterations
d8d93ee feat(next): run explicit clean-room campaigns
4ec3cda docs(next): record completed production gates
8ca4b8d docs(next): explain running without legacy cutover
```

## Start of the next session

1. Read this handoff and [Current Status and Known Issues](CURRENT_STATUS.md).
2. Confirm whether the goal is replicate-loop support, input conversion, a bounded
   pilot, the full experiment, analysis aggregation, or legacy cutover.
3. Inspect only the selected input files and their current Git status.
4. Run the full offline suite before any implementation change:

   ```bash
   .venv/bin/python -m pytest -q tests_next/unit tests_next/integration
   ```

5. Keep the next commit limited to that one explicitly selected task.
