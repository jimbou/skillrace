# SkillRACE Next

`skillrace_next` is the clean-room implementation of the two SkillRACE research
pipelines. It does not import the legacy `skillrace` package and it has not replaced the
legacy package.

The implementation deliberately uses explicit Python functions, frozen dataclasses,
JSON files, subprocesses, Docker commands, and sequential loops. It is not a workflow
framework or a general benchmark platform.

## Current status

Tasks 1–15 of the clean-room rebuild are implemented and committed. Task 16 now has the
small direct production composition for both loops: `part1 --live` accepts an existing
S0 and its provenance, while `part2 --live` accepts a public scenario and one or more
held-out test records. The Part II methods generate their own development tasks and
checks from the public scenario; no pre-authored development suite is supplied.

The new Part I and Part II commands passed separate real DeepSeek CLI contracts on
2026-07-18. Part II generated six development tasks, admitted one general Random repair,
and loaded/evaluated the hidden test only after all methods finished. The final bounded
gate then passed both Lab model tracks. Task 16 implementation and verification are
complete. Do not perform the package rename or legacy cutover yet.

See [Current status and known issues](docs/CURRENT_STATUS.md) before running paid tests.

## Use it now without cutover

`skillrace_next` is already the runnable implementation. It does not need to be renamed
to use skills and scenarios stored elsewhere in this repository. A normal working layout
is:

```text
skillrace_next/    new Python implementation
skills/            input skill directories, each containing SKILL.md
scenarios/         public scenarios, properties, environments, and held-out test assets
out/               generated skills, runs, checks, patches, and evidence
```

Run `python -m skillrace_next ...` from the repository root and pass paths under
`skills/` and `scenarios/` as CLI arguments. The pipeline reads those inputs; it does not
import the old `skillrace` package or require the inputs to live inside
`skillrace_next/`.

Existing scenario assets are directly usable when they satisfy the new input contracts:

- Part I needs an S0 directory with `SKILL.md`, an existing provenance receipt, and an
  ordered property JSON list.
- Part II needs a nonempty public scenario text file.
- Each Part II held-out test needs a strict `skillrace-test-case/1` JSON record whose
  paths and hashes identify its prompt, Docker environment, NL checks, and proposal
  receipt.

Legacy scenario `test.json` files are not silently interpreted or migrated. If an
existing held-out test uses a different schema, preserve its prompt/environment/check
assets and create the strict `TestCase` record beside them. See
[Configuration, providers, and CLI](docs/CONFIGURATION_AND_CLI.md#using-repository-skills-and-scenarios).

For repository-backed runs, set `config.suite_path` to the scenario/test asset root and
`config.output_root` to a separate directory under `out/`. For Part II, keep the config's
`scenario_path` equal to the file passed with `--scenario` so the frozen provenance is
unambiguous.

One CLI invocation runs `replicate_count` campaigns sequentially under numbered
`<output_root>/replicates/0001/`, `0002/`, and so on. Each replicate receives its own
effective config/output root and shares no method state with another replicate. For paid
runs, set config `live` to `true` as well as passing `--live`.

## Documentation

- [Pipeline and component reference](docs/PIPELINE.md)
- [Configuration, providers, and CLI](docs/CONFIGURATION_AND_CLI.md)
- [Verification, Docker, replay, and evidence](docs/VERIFICATION_AND_EVIDENCE.md)
- [Testing and live-contract operations](docs/TESTING.md)
- [Current status and known issues](docs/CURRENT_STATUS.md)
- [Handoff and remaining work](docs/HANDOFF.md)
- [Lab provider integration note](LAB_PROVIDER_DESIGN.md)

The approved design and task plan remain the scientific contract. The documents above
describe the implemented code and the remaining non-blocking operational notes.

## The two loops

Part I tests one immutable existing skill:

```text
for each method and discovery slot:
    propose test
    validate test and Docker image
    run immutable S0 with the weak Pi agent
    have Codex author executable checks locally
    execute those checks in the task container with docker exec
    update only that method's exploration state

group failures before repair
for each confirmed group:
    patch a fresh copy of S0 with the same-track Pi model
    replay with the exact saved check bundle
    record accepted, rejected, or unresolved
```

Part II evolves one generated skill independently for each method:

```text
generate one S0
for each method:
    copy the same S0
    for each development iteration:
        generate/select a development task from the public scenario and method state
        run the current Si
        have Codex author checks and execute them through docker exec
        update method state
        if checks fail:
            patch a copy of Si
            replay the failing test and retained regression tests
            carry the candidate forward only if it repairs at least one prior failure
            and turns no prior pass into a failure
after every method finishes:
    load the held-out tests for the first time
    evaluate S0 and every method's final skill on those tests
```

Within a model track, every non-verifier role uses the same configured provider/model.
Codex `gpt-5.6-terra` with medium reasoning is the checker author, not the patcher. The
checker scripts, executed through `docker exec`, produce the authoritative verdict.

## Supported model tracks

The current provider table supports:

- `yunwu/deepseek-v3.2`
- `lab/deepseek-v4-flash` (upstream ID `ds/deepseek-v4-flash`)
- `lab/qwen3.6-flash` (upstream ID `ali/qwen3.6-flash`)

The current final-development tracks are the two Lab models. Provider names and friendly
model names are stored separately so receipts do not confuse the two gateways.

## Public commands

```bash
python -m skillrace_next live-smoke \
  --config path/to/config.json \
  --component patcher \
  --live

python -m skillrace_next part1 \
  --config path/to/part1.json \
  --s0-dir skills/my-skill \
  --s0-receipt skills/my-skill/receipt.json \
  --skill-id my-skill \
  --properties scenarios/my-scenario/properties.json \
  --live

python -m skillrace_next part2 \
  --config path/to/part2.json \
  --scenario scenarios/my-scenario/scenario.md \
  --heldout-test scenarios/my-scenario/heldout/t1/test-case.json \
  --live

python -m skillrace_next analyze --run path/to/run
```

Paid tests require explicit `--live`.
Without `--live`, `part1` and `part2` only validate and freeze the configuration; they do
not spend provider budget.

## Safe starting checks

Offline tests do not spend provider budget:

```bash
.venv/bin/python -m pytest -q tests_next/unit tests_next/integration
```

Check the clean-room import boundary:

```bash
rg -n '(^|[[:space:]])(from|import)[[:space:]]+skillrace([[:space:].]|$)' \
  skillrace_next tests_next
```

The production CLI contracts and final bounded gate passed on 2026-07-18. Read
[Current status and known issues](docs/CURRENT_STATUS.md) before another paid run.

## No cutover

`skillrace_next` remains a separate package. Renaming it to `skillrace`, moving the legacy
implementation, or changing canonical entry points requires explicit user approval after
all offline, individual live, and final model gates are green.

This restriction does not prevent running `skillrace_next`, mounting or referencing
`skills/` and `scenarios/`, or writing new evidence under `out/`. It only prevents
replacing the old package and its existing entry points.
