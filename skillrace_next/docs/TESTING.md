# Testing and Live-Contract Operations

## Explicit paid-test gate

Tests under `tests_next/live/` are skipped unless pytest receives `--live`. Every paid
command must include that flag explicitly.

Do not substitute mocked responses for a required live contract. Do not use a later
end-to-end run as a substitute for an individual component contract.

Run commands from the repository root. The runnable package remains
`python -m skillrace_next`; no legacy-package cutover is required. Tests and experiments
may reference existing `skills/` and `scenarios/` paths as inputs. Do not rewrite those
directories merely to colocate them with the package.

Before a paid run using an existing scenario, confirm that each held-out argument is a
strict `skillrace-test-case/1` record rather than an older scenario-specific `test.json`.
Freezing a Part I/II config without `--live` is the safe way to validate the config and
output location without making provider calls.

## Offline verification

Run all unit and integration tests:

```bash
.venv/bin/python -m pytest -q tests_next/unit tests_next/integration
```

Useful focused commands:

```bash
.venv/bin/python -m pytest -q tests_next/unit/test_cli.py \
  tests_next/unit/test_documented_cli.py

.venv/bin/python -m pytest -q tests_next/integration/test_part1_loop.py \
  tests_next/integration/test_part2_loop.py \
  tests_next/integration/test_heldout_isolation.py
```

The 2026-07-23 contextual episode/tree cycle had 245 passing unit/integration tests. This
count records that run; rerun the command after future code changes.

### Generic Pi runtime image

The first test in `test_pi_runtime_live.py` performs a local metadata-only rebuild of
`skillrace/pi-runtime:0.73.1`. It inspects the legacy pinned image ID, gives that exact ID
a hash-derived local source tag, rebuilds only model-neutral OCI metadata, and records the
old/new IDs under `out/live-contracts/pi-runtime-image/`. It does not repeat the expensive
Pi/npm installation.

Run that build contract before offline Docker integration tests on a machine that does
not yet have the generic tag:

```bash
.venv/bin/python -m pytest \
  tests_next/live/test_pi_runtime_live.py::test_generic_pi_runtime_image_is_rebuilt_from_pinned_local_image \
  --live -v -s
```

## Individual live contracts

Run each file separately:

```bash
.venv/bin/python -m pytest tests_next/live/test_pi_runtime_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_task_runner_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_test_proposer_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_codex_verifier_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_check_executor_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_episode_creator_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_tree_merge_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_skillrace_proposal_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_verigrey_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_skill_generation_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_patcher_live.py --live -v -s
.venv/bin/python -m pytest tests_next/live/test_exact_replay_live.py --live -v -s
```

Provider-specific transport contracts are in
`tests_next/live/test_lab_provider_live.py`. Bounded campaign contracts are in
`test_part1_tiny_live.py` and `test_part2_tiny_live.py`.

The fixed Part I study bundle has its own real contract:

```bash
.venv/bin/python -m pytest \
  tests_next/live/test_cli_campaign_live.py::test_real_part1_prepared_s0_and_properties_contract \
  --live -v -s
```

The inspected passing evidence is
`out/live-contracts/part1-study-inputs/deepseek-v4-flash/20260720T081754Z-03ff7db6/`.

The frozen Part II study bundle has a separate real generation/development/held-out
contract:

```bash
.venv/bin/python -m pytest \
  tests_next/live/test_cli_campaign_live.py::test_real_part2_prepared_scenario_and_heldout_contract \
  --live -v -s
```

The inspected passing evidence is
`out/live-contracts/part2-study-inputs/deepseek-v4-flash/20260720T084119Z-9a9369c2/`.

## What each live contract proves

| Contract | Real boundary exercised |
|---|---|
| Pi runtime | Model-neutral pinned image build, direct provider response, and Pi tool use |
| Task runner | Weak Pi agent inside a real task container, durable artifact/trace |
| Test proposer | Same-track Pi proposal followed by deterministic Docker validation |
| Codex verifier | Terra/medium over read-only inputs produced by a real task run |
| Check executor | Real Codex-authored scripts executed through `docker exec` |
| Episode creator | Same-track Pi segmentation grounded in a real trace |
| Tree merge | Deterministic merge plus same-track Pi alignment when ambiguous |
| SkillRACE proposal | Tool-free selector over a long observed-edge index, host branch isolation, fresh tool-free mutator, and deterministic Docker validation |
| VeriGrey | Real proposal from saved tool-transition coverage state |
| Skill generation | Same-track Pi creates and isolates one S0 |
| Patcher | Same-track Pi edits only `SKILL.md` from real failure evidence |
| Exact replay | Fresh weak-agent run with the exact saved scripts and acceptance rule |

The 2026-07-23 contextual episode/tree gates passed independently for both
`deepseek-v4-flash` and `qwen3.6-flash`. Fresh inspected roots are:

```text
out/live-contracts/episode-creator/<model>/20260723T*/
out/live-contracts/tree-merger/<model>/20260723T*/
out/live-contracts/skillrace-edge-selector/<model>/20260723T*/
out/live-contracts/part1/<model>/20260723T*/
out/live-contracts/part2/<model>/20260723T*/
```

The exact successful run IDs are recorded in `CURRENT_STATUS.md`. The full 30-test study
has not been run.

The checker contract must use Codex, not the cheap provider model. The patcher contract
must use the cheap track model, not Codex.

## Manual semantic inspection

Schema-valid JSON is insufficient. For the first live output of each kind, inspect:

- proposer: task is concrete, self-contained, and targets the declared property/branch;
- episode list: ranges are ordered, grounded, and describe actual reasoning/tool events;
- tree: node membership, edge reasons, failures, and reach status are meaningful;
- generated skill: instructions are general and internally coherent;
- patch: change addresses the diagnostic generally and only touches `SKILL.md`;
- verifier: scripts measure the NL property without repairing the artifact;
- replay: new artifact and results really came from the candidate skill and exact bundle.

Record the evidence directory used for inspection.

## Final dual-model gate

The current gate command is:

```bash
.venv/bin/python -m pytest \
  tests_next/live/test_dual_model_gate_live.py \
  --live -v -s
```

It performs fresh direct and Pi preflights, then bounded Part I and Part II slices for:

- `deepseek-v4-flash`; and
- `qwen3.6-flash`.

### 2026-07-18 result

The most recent gate passed both parameterized cases in 25 minutes 30 seconds. Each track
completed a fresh direct preflight, Pi tool preflight, Part I slice, and Part II slice.

```text
out/live-contracts/dual-model-gate/deepseek-v4-flash/
  20260718T021119Z-de8da6fc/
out/live-contracts/dual-model-gate/qwen3.6-flash/
  20260718T022206Z-49824891/
```

Both tracks recorded Random `accepted, rejected`, retained S1 after the rejected second
candidate, and evaluated S0 plus all final skills only after development. In each track,
S0 failed the bounded hidden test and Random S1 passed; VeriGrey and SkillRACE retained
S0 and therefore also failed. These are contract-fixture outcomes, not a broad comparison
of the methods.

The active Lab credential was absent from gate and child evidence, and the run ended with
no owned or unrelated running containers.

## Provider failure versus behavioral failure

Stop the gate on a persistent 429/5xx, missing provider capability, invalid credentials,
or repeated malformed provider boundary response. Preserve a blocked receipt.

Do not classify these as provider failures:

- weak agent times out;
- weak agent ignores the skill;
- artifact is incorrect;
- patcher produces a wrong generalization; or
- exact replay rejects the candidate.

Those are experimental outcomes. Preserve their artifacts and traces and let the
authoritative checks decide them.

## Docker cleanup check

After each live contract, inspect only SkillRACE-owned containers before removing
anything:

```bash
docker ps --format '{{.ID}} {{.Names}} {{.Status}}'
```

Resolve the mounts/labels of a suspected leftover with `docker inspect` before cleanup.
Never delete unrelated containers. The 2026-07-17 final gate ended with no running
containers.

## Evidence secret scan

Use the credential from the environment without printing it:

```bash
if test -n "${LAB_KEY_UNLIMITED:-}"; then
  rg -l -F -- "$LAB_KEY_UNLIMITED" out/live-contracts
fi
```

No output is the expected result. The final gate and all of its Part I/Part II child
evidence passed the exact active-key scan. The previously exposed credential was rotated
before these runs.
