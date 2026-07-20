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
| `replicate_count` | Number of independent sequential campaign repetitions |
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
| `scenario_path` | Frozen Part II scenario provenance; must match explicit `--scenario` |
| `iteration_budget` | Sequential discovery/improvement slots |
| `live` | Frozen provenance flag; paid runs must also pass `--live` |
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

One command invocation runs `replicate_count` campaigns sequentially. Outputs are stored
under `<output_root>/replicates/0001/`, `0002/`, and so on. Each iteration receives a
separate effective output root and new campaign-local state while preserving identical
scientific inputs. CLI arguments are authoritative when they duplicate config fields:
the presence of `--live` supplies effective `live`, and Part II `--scenario` supplies the
effective `scenario_path`. If a source value disagrees, the CLI writes a warning to
stderr and freezes the effective value it actually uses.

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

The shared model-independent image tag is `skillrace/pi-runtime:0.73.1`. Its final OCI
metadata records `org.skillrace.track.model=runtime-mounted`; the actual DeepSeek, Qwen,
or Yunwu catalog is mounted by `run_pi` for each invocation.

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

## Using repository skills and scenarios

The package and experiment data are deliberately separate:

```text
skillrace_next/                 runnable clean-room package
skills/<skill>/SKILL.md         existing or generated skill input
scenarios/<scenario>/           public scenario and test assets
out/<experiment>/               immutable run/evidence output
```

No package rename is required. Invoke `python -m skillrace_next` from the repository root
and pass the data paths explicitly. `skillrace_next` never imports the old `skillrace`
package.

For repository-backed inputs, configure:

```json
{
  "suite_path": "scenarios/my-scenario",
  "scenario_path": "scenarios/my-scenario/scenario.md",
  "output_root": "out/my-experiment"
}
```

`suite_path` must contain every external held-out prompt, environment, NL-check file,
and proposal receipt. `output_root` must be separate because it receives generated tests
and evidence. For Part II, `--scenario` is the executed input. If source config
`scenario_path` differs, the CLI warns and freezes the argument path as effective
provenance.

For Part I, a repository skill is usable when `--s0-dir` contains `SKILL.md`.
`--s0-receipt` must name the existing provenance receipt, and `--properties` must contain
an ordered nonempty JSON list such as:

```json
[
  {
    "property_id": "P1",
    "description": "The requested artifact has the exact required content."
  }
]
```

For Part II, `--scenario` may point directly at a nonempty scenario text or Markdown file
under `scenarios/`. It is the public input used both to generate S0 and to seed each
method's development-test creation. There is no external development-suite argument.

Held-out tests are stricter. Each `--heldout-test` must name one serialized
`skillrace-test-case/1` object with exactly these record fields:

| Field | Requirement |
|---|---|
| `schema` | `skillrace-test-case/1` |
| `test_id` | Stable unique string |
| `prompt_path`, `prompt_hash` | Prompt path and SHA-256 |
| `environment_directory`, `environment_hash` | Docker environment path and tree hash |
| `nl_check_path`, `nl_check_hash` | Ordered NL-check JSON path and SHA-256 |
| `origin_method` | Provenance label such as `heldout` |
| `proposal_receipt` | Existing receipt path |
| `validation_status` | May be `pending`; the loader validates again |
| `validation_diagnostic` | Usually an empty string before validation |
| `container_image_id` | May be empty; validation records the built image ID |

Relative asset paths are resolved against the directory containing `test-case.json`.
Absolute paths are also accepted. Hashes must describe the referenced files exactly.
A practical layout is:

```text
scenarios/my-scenario/
├── scenario.md
├── properties.json
└── heldout/t1/
    ├── test-case.json
    ├── prompt.txt
    ├── nl_checks.json
    ├── proposal.json
    └── environment/
        ├── Dockerfile
        └── sanity.json
```

Existing prompts, environments, NL checks, and receipts can be reused. An older
scenario-level `test.json` with another schema is not a `TestCase` record and is not
automatically converted; create the strict record beside those assets instead of adding
a compatibility layer.

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
```

Without `--live`, both commands validate/freeze the config and write a `command.json`
status of `config_frozen`. Campaign-specific arguments are required only when `--live`
is present, so freezing a config never starts paid work. Because the CLI flag is
authoritative, omitting `--live` freezes effective `live: false`; a contradictory source
value produces a warning.

For Part I, `--s0-dir` contains the immutable input `SKILL.md`, `--s0-receipt` is its
existing provenance receipt, `--skill-id` supplies its stable identity, and
`--properties` is a JSON list of properties used by the three test creators. Discovery
always runs that exact S0; a repair never changes later discovery runs.

For Part II, `--scenario` is the public original scenario. It is given to the base-skill
generator and is also the initial natural-language property from which Random, VeriGrey,
and SkillRACE create their own development tasks. Each generated task contains its own
prompt, Docker environment, NL check, proposal receipt, and later Codex-authored
executable check bundle under that method and iteration's evidence directory. There is
no Part II development-suite argument.

Each `--heldout-test` names a strict serialized `TestCase` record. The option is
repeatable. Relative prompt/environment/NL-check/receipt paths are resolved relative to
the record file. The command deliberately does not open these records until S0 and all
three final method skills have been produced. It then evaluates S0 and every final skill
on identical held-out cells.

`live-smoke --component part1|part2` remains the separate bounded component contract; it
does not replace these production campaign commands.

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
to the caller. If a production Part I/II campaign raises, the CLI first writes terminal
status `failed` and then re-raises the original exception.

## Credential safety

Never put credentials in configuration files, prompts, test fixtures, or checked-in
evidence. Provider keys enter Docker only by environment-variable name. Runtime stdout
and stderr are redacted before being saved.

The dual-gate helper reads the Lab secret from its environment rather than receiving it as
a pytest-visible function argument, and sanitizes captured child output before saving it.
Codex invocation removes both supported provider credentials from its inherited host
environment. The exposed 2026-07-17 Lab key was rotated before the green 2026-07-18 gate.
