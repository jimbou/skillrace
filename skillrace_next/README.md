# SkillRACE Next

`skillrace_next` is the clean-room implementation of the two SkillRACE research
pipelines. It does not import the legacy `skillrace` package and it has not replaced the
legacy package.

The implementation deliberately uses explicit Python functions, frozen dataclasses,
JSON files, subprocesses, Docker commands, and sequential loops. It is not a workflow
framework or a general benchmark platform.

## Current status

Tasks 1–15 of the clean-room rebuild are implemented and committed. The individual live
contracts have been exercised with real Pi/provider calls, real Codex checker authoring,
and real Docker checker execution.

Task 16 is not complete. Its CLI and documentation changes are still uncommitted, and
the final two-model gate exposed real test-harness and lifecycle problems. Do not perform
the package rename or legacy cutover yet.

See [Current status and known issues](docs/CURRENT_STATUS.md) before running paid tests.

## Documentation

- [Pipeline and component reference](docs/PIPELINE.md)
- [Configuration, providers, and CLI](docs/CONFIGURATION_AND_CLI.md)
- [Verification, Docker, replay, and evidence](docs/VERIFICATION_AND_EVIDENCE.md)
- [Testing and live-contract operations](docs/TESTING.md)
- [Current status and known issues](docs/CURRENT_STATUS.md)
- [Lab provider integration note](LAB_PROVIDER_DESIGN.md)

The approved design and task plan remain the scientific contract. The documents above
describe the code that currently exists, including where it does not yet satisfy that
contract.

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
        select a test
        run the current Si
        execute predefined checks
        update method state
        if checks fail:
            patch a copy of Si
            replay the failing test and retained regression tests
            carry the candidate forward only if deterministic acceptance passes
    evaluate the final skill and S0 on held-out tests
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

python -m skillrace_next part1 --config path/to/part1.json
python -m skillrace_next part2 --config path/to/part2.json
python -m skillrace_next analyze --run path/to/run
```

Paid tests require explicit `--live`.

Important: the present `part1` and `part2` CLI commands validate and freeze a config.
With `--live`, they invoke the corresponding bounded live-contract test. They do not yet
construct and run an arbitrary campaign from the supplied suite/scenario configuration.
This is a Task 16 blocker, not the intended final CLI behavior.

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

Do not run the final live gate until the P0 issues in
[Current status and known issues](docs/CURRENT_STATUS.md) are fixed and the Lab credential
used by the failed gate has been rotated.

## No cutover

`skillrace_next` remains a separate package. Renaming it to `skillrace`, moving the legacy
implementation, or changing canonical entry points requires explicit user approval after
all offline, individual live, and final model gates are green.
