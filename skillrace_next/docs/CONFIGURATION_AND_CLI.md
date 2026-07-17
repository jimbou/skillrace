# Configuration, Providers, and CLI

## Configuration format

Configurations are strict JSON objects using schema
`skillrace-experiment-config/1`. Unknown fields, missing fields, unsupported provider/model
pairs, invalid part names, non-Codex verifier backends, incomplete timeout maps, and
nonpositive counts are rejected.

The complete field set is:

| Field | Meaning |
|---|---|
| `experiment_id` | Stable run label |
| `part` | `part1` or `part2` |
| `methods` | Ordered method names, normally Random, VeriGrey, SkillRACE |
| `replicate_count` | Requested independent repetitions |
| `provider`, `model_id` | One supported non-verifier model track |
| `pi_version` | Pinned Pi version recorded for provenance |
| `role_budgets` | Turn limits for proposer, weak agent, segmenter, alignment, generator, patcher |
| `verifier_backend` | Must be `codex` |
| `verifier_command` | Command prefix, normally `codex exec` |
| `verifier_model` | Current contract uses `gpt-5.6-terra` |
| `verifier_reasoning` | Current contract uses `medium` |
| `docker_image` | Pinned Pi/runtime base image |
| `resource_limits` | Container CPU and memory values |
| `network_policy` | Task-container Docker network mode |
| `timeouts` | Exact provider, Pi, Docker, Codex, check, and patch limits |
| `suite_path` | Development/test suite root |
| `scenario_path` | Part II public scenario path |
| `iteration_budget` | Sequential discovery/improvement slots |
| `live` | Frozen configuration provenance flag |
| `output_root` | Directory receiving frozen config and command receipt |
| `heldout_repetitions` | Part II repetitions per held-out cell |

See `tests_next/fixtures/development.deepseek-v3.2.json` for a complete example. That
fixture is a schema/development example; its paths are not a production benchmark suite.

At command start, `freeze_config` writes:

```text
<output_root>/config.json
<output_root>/config.sha256
```

The hash is computed from canonical normalized JSON. A running stage must use the frozen
configuration rather than rereading a mutable source file.

## Providers and model identity

Supported pairs are intentionally explicit:

| Config pair | Upstream model ID | Credential variable |
|---|---|---|
| `yunwu/deepseek-v3.2` | `deepseek-v3.2` | `yunwu_key` |
| `lab/deepseek-v4-flash` | `ds/deepseek-v4-flash` | `LAB_KEY_UNLIMITED` |
| `lab/qwen3.6-flash` | `ali/qwen3.6-flash` | `LAB_KEY_UNLIMITED` |

`resolve_model` rejects every other pair. Receipts record provider, friendly model,
provider-qualified identity, and upstream model ID.

Known Lab token rates are stored directly in the small provider table. If a required
rate is unavailable—currently cache-write pricing—the estimate is recorded as
`unpriced`; the code does not invent a price. Yunwu usage is also `unpriced` unless a
provider-reported cost is available.

## Model roles

Within one track, the configured cheap provider/model is used for every non-verifier
model role:

- test proposer;
- weak task agent;
- episode segmenter;
- tree alignment;
- Part II base-skill generator; and
- skill patcher.

Codex is the only verifier model. Check execution and patch acceptance are deterministic
Python/Docker operations with no model.

## Direct and Pi preflight

`direct_provider_preflight` sends one exact-response chat-completion request and preserves
a sanitized `preflight.json`. It retries once only for a transient 429/5xx, transport
failure, timeout, or malformed response.

`run_pi` launches the pinned Pi SDK in an ephemeral Docker container, mounts a minimal
model catalog, permits an explicit tool set and turn count, captures the session trace,
tool events, usage, stdout/stderr, timeout, and receipt, and removes the invocation
container through Docker `--rm`.

Pi role calls use at most one SDK-level retry. Weak experimental task executions are not
automatically retried.

## Public CLI

Only four commands are public:

```text
python -m skillrace_next live-smoke
python -m skillrace_next part1
python -m skillrace_next part2
python -m skillrace_next analyze
```

Internal stages such as checker authoring, replay, or patching are intentionally not
public commands.

### `live-smoke`

```bash
python -m skillrace_next live-smoke \
  --config path/to/config.json \
  --component <name> \
  --live
```

Supported component names:

```text
pi-runtime task-runner test-proposer codex-verifier check-executor
episode-creator tree-merge skillrace-proposal verigrey skill-generation
patcher exact-replay part1 part2
```

The command freezes the config, invokes the named pytest live file, and writes
`command.json` with `passed` or `failed`. Omitting `--live` raises an error before paid
work begins.

### `part1` and `part2`

```bash
python -m skillrace_next part1 --config path/to/part1.json
python -m skillrace_next part2 --config path/to/part2.json
```

Without `--live`, the current implementation validates/freezes the config and writes a
`command.json` status of `config_frozen`.

With `--live`, it runs the corresponding bounded pytest contract and writes `passed` or
`failed`.

This is not the final intended behavior. Task 16 requires these commands to build the
concrete callbacks/suite inputs and invoke `run_part1` or `run_part2` for the supplied
configuration. The present CLI runs development fixtures instead. See `P0-3` in
[Current status and known issues](CURRENT_STATUS.md).

### `analyze`

```bash
python -m skillrace_next analyze --run path/to/run
```

The current command reads `<run>/summary.json`, verifies it has a summary object, and
writes `<run>/analysis.json` containing the source schema and copied summary. It does not
reconstruct missing metrics or aggregate multiple cells.

## `command.json`

Command receipts use schema `skillrace-command/1`. They record command, live flag, status,
and component for `live-smoke`. A failed live subprocess returns the same nonzero status
to the caller.

## Credential safety

Never put credentials in configuration files, prompts, test fixtures, or checked-in
evidence. Provider keys enter Docker only by environment-variable name. Runtime stdout
and stderr are redacted before being saved.

Two current credential defects must be fixed before another final gate:

1. A failed dual-gate helper included the secret-valued argument in pytest's traceback.
2. Codex invocation removes `yunwu_key` but does not yet remove `LAB_KEY_UNLIMITED` from
   its inherited host environment.

The Lab key used by the failed 2026-07-17 gate should be rotated. Do not copy that raw
traceback into documentation or issue trackers.
