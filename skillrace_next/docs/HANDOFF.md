# SkillRACE Next Handoff

Date: 2026-07-21

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

The original study inputs and bounded pilot are prepared and complete. The full-study
scale is now fixed, but approved development-test revisions must be implemented and
live-verified before freezing or running the full-study configs. Follow
[Full-Study Remaining TODO](FULL_STUDY_REMAINING_TODO.md). The production CLI executes
`replicate_count` independent campaigns sequentially in numbered directories.

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
- Before weak execution, files baked into the validated image's `/workspace` are copied
  into the durable host artifact and then mounted back. This preserves supplied projects
  and test harnesses while keeping the final artifact inspectable and immutable.
- Weak-agent timeout is an experimental outcome. Its partial artifact is checked without
  retrying the weak agent for luck.
- Repairable environment conditions are task behavior. A checker-only missing dependency
  is inconclusive, but a prompt-required exact launcher that a root agent could repair is
  a failure and must still yield structured checker JSON. Qwen proved direct S0 repair;
  DeepSeek proved same-track failure, patching, exact replay, and no-regression admission.

## Required next work for full experiments

The completed live gates are bounded contracts. They prove the pipeline boundaries but
are not a full scientific run over the repository's skills and scenarios.

### 0. Replicate execution is complete

One `part1` or `part2` invocation executes `ExperimentConfig.replicate_count` independent
skill/scenario/model campaigns sequentially. Outputs use:

```text
<output_root>/replicates/0001/
<output_root>/replicates/0002/
...
```

Each replicate receives a fresh effective config/output root, identical input arguments,
and no state from another replicate. There is intentionally no scheduler, parallel
campaign manager, or multi-skill/scenario/model matrix runner.

### 1. Choose the full-study scale

The 30 Part I skills, ten Part II scenarios, and 100 held-out records are already selected
and hash-bound. The next operator must still choose, explicitly:

- iteration budgets, held-out repetitions, and the explicit replicate/cell schedule; and
- which model tracks are included in the actual study.

The implemented final-development tracks are:

- `lab/deepseek-v4-flash`; and
- `lab/qwen3.6-flash`.

`yunwu/deepseek-v3.2` remains supported. Within a track, do not mix cheap models across
proposer, weak-agent, segmenter, alignment, generator, or patcher roles.

### 2. Part I inputs are prepared

The approved 30-skill ordered selection, exclusions, normalized properties, and
hash-bound receipts are under:

```text
skillrace_next/study/part1/selection.json
skillrace_next/study/part1/<skill>/properties.json
skillrace_next/study/part1/<skill>/s0-receipt.json
```

The selected S0 itself remains unchanged under `skills/<skill>/`. Receipts point to and
bind that source tree; no skill copy was made. Before launching any campaign, call
`verify_part1_study` to reject any S0 or prepared-property hash drift. The separate real
contract evidence is under
`out/live-contracts/part1-study-inputs/deepseek-v4-flash/20260720T081754Z-03ff7db6/`.

Example invocation:

```bash
python -m skillrace_next part1 \
  --config path/to/part1-config.json \
  --s0-dir skills/file-check \
  --s0-receipt skillrace_next/study/part1/file-check/s0-receipt.json \
  --skill-id file-check \
  --properties skillrace_next/study/part1/file-check/properties.json \
  --live
```

### 3. Part II held-out records are prepared

The complete repository D2 suite is frozen under:

```text
skillrace_next/study/part2/selection.json
skillrace_next/study/part2/<scenario>/scenario.md
skillrace_next/study/part2/<scenario>/heldout/<test>/test-case.json
```

It contains all ten selected scenarios and 100 strict held-out records. During
development, each method still generates its own prompt, Docker environment, and NL
checks from the public scenario. The frozen records are separate final-evaluation inputs
and are not loaded until development finishes.

Each record binds the exact prompt, Docker environment, artifact-readable NL checks, and
source receipt. The receipt and selection manifest also bind the original test contract,
candidate, oracle evidence, and all 192 audited legacy scripts. These copied scripts are
review provenance only; real Codex authors executable evaluation scripts from the prompt,
artifact, trace, and fixed NL checks.

Run `verify_part2_study` before creating campaign configs. It verifies the frozen bundle
without consulting the mutable source `scenarios/` tree. The inspected real contract is
under
`out/live-contracts/part2-study-inputs/deepseek-v4-flash/20260720T084119Z-9a9369c2/`.

The existing `skills/` and `scenarios/` trees were not modified during the clean-room
work. They are outside the prior write scope and contain substantial unrelated dirty
changes. Editing or converting them requires a new explicit instruction and careful,
file-specific commits.

Example invocation:

```bash
python -m skillrace_next part2 \
  --config path/to/part2-config.json \
  --scenario skillrace_next/study/part2/text-template/scenario.md \
  --heldout-test skillrace_next/study/part2/text-template/heldout/t1/test-case.json \
  --live
```

### 4. Create real experiment configs

For repository-backed inputs, use separate input and output roots:

```json
{
  "suite_path": "skillrace_next/study/part2/text-template",
  "scenario_path": "skillrace_next/study/part2/text-template/scenario.md",
  "output_root": "out/part2-text-template"
}
```

The complete strict config has additional required fields documented in
[Configuration, Providers, and CLI](CONFIGURATION_AND_CLI.md). In Part II, keep the
frozen `scenario_path` equal to the explicit `--scenario` argument.

CLI override behavior is implemented. The presence of `--live` is authoritative for paid
execution, and Part II `--scenario` is authoritative for the public scenario. When a
source config disagrees, the CLI prints a warning and freezes the effective CLI value so
provenance describes what actually ran. Omitting `--live` always prevents paid work and
freezes effective `live: false`.

Before spending provider budget, freeze each config without `--live`:

```bash
python -m skillrace_next part1 --config path/to/part1-config.json
python -m skillrace_next part2 --config path/to/part2-config.json
```

Use a fresh `output_root` for each actual run. Do not reuse or overwrite a completed run
directory.

### 5. Run and inspect the actual experiments

The bounded DeepSeek pilot is complete across immutable schedules `pilot-v3` through
`pilot-v8`; each fresh schedule was used only after a documented infrastructure
correction. The final cell and authoritative harness-preservation evidence are under
`out/live-contracts/pilot-v8/deepseek-v4-flash/part2/fix-failing-test/`. Do not rerun
completed pilot cells or resume interrupted predecessor roots.

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

## Recorded later work and non-work

These items do not block a single campaign. Final aggregation remains a study TODO;
cutover and line-count refactoring do not.

### Legacy package cutover

Cutover would rename or install `skillrace_next` as `skillrace`, move/archive the old
implementation, and update canonical imports and entry points. It is not planned for this
study. Run `skillrace_next` directly and leave the legacy package untouched rather than
deleting it.

### Runtime image naming

Complete. Pi uses `skillrace/pi-runtime:0.73.1`, whose final OCI labels contain no model
name and record the model catalog as runtime-mounted. The metadata-only rebuild reused a
hash-tagged exact local source image rather than rebuilding Pi/npm layers. Old/new image
IDs are preserved under
`out/live-contracts/pi-runtime-image/20260720T080623Z-08a6e6aa/`. Fresh DeepSeek and Qwen
Pi contracts passed under:

```text
out/live-contracts/lab-provider/deepseek-v4-flash/20260720T080649Z-5b696d04/
out/live-contracts/lab-provider/qwen3.6-flash/20260720T080700Z-80109b00/
```

### Thin `analyze` command

`python -m skillrace_next analyze` copies an existing run summary into `analysis.json`.
It does not verify or aggregate multiple experiment cells. After the final output layout
is known, write one small Python script that reads all expected campaign summaries and
computes the chosen key metrics, averages, and comparisons. No analysis framework or
incomplete-run recovery system is needed.

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

- The full current offline suite passes.
- Separate real production Part I and Part II CLI contracts passed.
- Individual live contracts passed for provider/Pi, task execution, proposal, Codex
  verifier, Docker checker execution, episode creation, tree merge/alignment, SkillRACE
  proposal, VeriGrey proposal, S0 generation, patching, and exact replay.
- The final DeepSeek/Qwen gate passed `2` parameterized cases in `25m30s`.

Evidence:

```text
out/live-contracts/cli-part1/deepseek-v4-flash/20260718T012237Z-997c25ac/
out/live-contracts/cli-part2/deepseek-v4-flash/20260718T014531Z-1d582fa0/
out/live-contracts/cli-replicates/deepseek-v4-flash/20260720T073745Z-0f3da0bc/
out/live-contracts/cli-replicates/deepseek-v4-flash/20260720T075536Z-5f9f9295/
out/live-contracts/codex-verifier/20260720T075240Z-a6dc5088/
out/live-contracts/check-executor/20260720T075414Z-f3ced823/
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
2. Confirm whether the next goal is Part I input selection, Part II input preparation,
   the bounded pilot, the full experiment, or final aggregation.
3. Inspect only the selected input files and their current Git status.
4. Run the full offline suite before any implementation change:

   ```bash
   .venv/bin/python -m pytest -q tests_next/unit tests_next/integration
   ```

5. Keep the next commit limited to that one explicitly selected task.
