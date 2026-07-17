# Testing and Live-Contract Operations

## Explicit paid-test gate

Tests under `tests_next/live/` are skipped unless pytest receives `--live`. Every paid
command must include that flag explicitly.

Do not substitute mocked responses for a required live contract. Do not use a later
end-to-end run as a substitute for an individual component contract.

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

The last recorded full offline run before the final gate had 133 passing unit/integration
tests. Run it again after the known issues are fixed; do not treat that historical count
as current proof.

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

## What each live contract proves

| Contract | Real boundary exercised |
|---|---|
| Pi runtime | Direct provider response and Pi tool use |
| Task runner | Weak Pi agent inside a real task container, durable artifact/trace |
| Test proposer | Same-track Pi proposal followed by deterministic Docker validation |
| Codex verifier | Terra/medium over read-only inputs produced by a real task run |
| Check executor | Real Codex-authored scripts executed through `docker exec` |
| Episode creator | Same-track Pi segmentation grounded in a real trace |
| Tree merge | Deterministic merge plus same-track Pi alignment when ambiguous |
| SkillRACE proposal | Real proposal targeting a saved unreached branch |
| VeriGrey | Real proposal from saved tool-transition coverage state |
| Skill generation | Same-track Pi creates and isolates one S0 |
| Patcher | Same-track Pi edits only `SKILL.md` from real failure evidence |
| Exact replay | Fresh weak-agent run with the exact saved scripts and acceptance rule |

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

### 2026-07-17 result

The most recent gate ran for 47 minutes 22 seconds and failed both parameterized cases.
Both models passed direct preflight, Pi preflight, and Part I. Both Part II subprocesses
completed their campaigns but failed a rigid expected-transition assertion.

DeepSeek evidence:

```text
out/live-contracts/dual-model-gate/deepseek-v4-flash/
  20260717T144314Z-7526e007/
```

DeepSeek's patch changed the lower-middle rule to upper-middle. Exact replay produced
`100` where the checker required the arithmetic mean `51`; the candidate was correctly
rejected and S0 retained.

Qwen evidence:

```text
out/live-contracts/dual-model-gate/qwen3.6-flash/
  20260717T150808Z-d34bb374/
```

Qwen's generated S0 documented the wrong lower-middle rule, but the weak task agent
ignored it and directly produced the correct standard median `51`. No patch was needed,
so the pipeline correctly recorded `retained`, followed by a deliberately rejected
second iteration.

The gate expected `accepted, rejected` for every track. The observed correct transitions
were DeepSeek `rejected, rejected` and Qwen `retained, rejected`. This makes the gate
fixture stochastic and over-specific; it does not show that the pipeline accepted a bad
patch.

Do not rerun this gate until the credential traceback issue, Codex environment issue,
replay timeout lifecycle, and gate criterion are fixed.

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

No output is the expected result. The most recent saved evidence passed this check, but
the outer pytest failure traceback itself exposed the value in terminal output. Rotate
that credential before more live work.
