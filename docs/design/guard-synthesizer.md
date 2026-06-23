<a href="../../README.md"><img src="../../skillrace-icon.png" alt="SkillRACE" width="54" align="right"></a>

# Component 5 — Guard Extractor & Test Synthesizer

> **Design spec** (Component 5, from the tex) — not yet implemented.

> tex §7 ("Guards and test synthesis"). **Cheap model + code validator.** This is
> the conceptual core: reasoning proposes the branch condition; execution confirms
> or refutes it.
>
> The component has **three separable sub-units**, each independently testable:
> **(5a) Guard Extractor** · **(5b) Candidate Synthesizer** · **(5c) Validator**.
> 5c is plain code + Docker and is the efficiency keystone ("LLM proposes, checker
> disposes").

---

## Purpose

At a **branch** (a node where runs that reached the same situation went different
ways), the Guard Extractor distills the **condition** that explains the split. The
Synthesizer then **negates or mutates** that condition to draft a new input
`(x*, E_0*)` aimed at an unexplored sibling region. The Validator **builds the
candidate container and checks the mutated condition holds — with no agent
involved** — so an agent run is spent only on inputs already proven to set up the
targeted branch. Only guards **decidable from the initial setup** (`E_0`-guards)
are targeted in v1; guards that could only be checked by running the agent are
deferred and counted.

---

## The two distinct guard signals (do not conflate them)

A guard is read from **two different signals with different sources** (tex §7):

| Signal | Source | What it tells us |
|--------|--------|------------------|
| **A — prior outcome** | the `result` field of the **just-finished episode's** summary, read from its **tool outputs** ([summarizer](./episode-summarizer.md)) | *what state the agent was in* |
| **B — next reasoning** | the **opening reasoning of the next episode** (`Episode.opening_reasoning`), read from the agent's **reasoning text** | *why it chose the branch it did, given that state* |

They are not the same source even though the agent's reasoning usually *restates*
the outcome. **The outcome is taken from the observation (Component 3), not from the
reasoning.** When the two **disagree** — the test *observably* failed but the next
reasoning proceeds as if it passed — that disagreement is **not noise to reconcile;
it is a bug signal** (the agent ignored a real failure), recorded and handed to the
property checker. The guard is **never** read from the *internal tool calls* of an
episode — those are tactics the tree abstracts over (which is exactly why two runs
that reached the same outcome by different tactics present the same situation).

---

## 5a. Guard Extractor

### Input
A branch from the [`BehaviorTree`](../data-contracts.md#6-behaviortree--state-of-component-4):
the node and its ≥2 out-edges, plus, for each diverging side, the
[`Episode`](../data-contracts.md#5-episode--the-trees-atom-segmentation--summary)
that left it (giving signal A = prior `result`, signal B = next `opening_reasoning`)
and a **diff of the diverging sides' initial environments/observations**.

### Output
A [`Guard`](../data-contracts.md#8-guard--output-of-the-guard-extractor):

```json
{
  "guard_id": "g1", "branch_id": "b1", "node_id": "n1",
  "condition": "the failing test is an import error (module/name cannot be imported)",
  "signals": {
    "prior_outcome": "failed with ImportError: cannot import name 'verify_token' from 'auth'",
    "next_reasoning": "The import fails because auth.py has no verify_token. Let me read auth.py.",
    "env_diff": "side A: auth.py lacks verify_token; side B: auth.py defines it but an assertion fails"
  },
  "grounding": { "kind": "executable", "check": "pytest -q 2>&1 | grep -q 'ImportError\\|ModuleNotFoundError'", "decidable_from": "E0" },
  "value_space": { "type": "multivalued", "observed": "the test fails with an import error", "siblings": ["the test fails with an assertion error", "the test times out", "test collection errors"] }
}
```

The extractor produces the condition **plus, wherever possible, an executable
grounding**: a concrete check like `test -f <path>`, "exit code ≠ 0", or "output
contains `ModuleNotFoundError`". Executable-grounded guards are cheap to satisfy and
verify; natural-language guards are evaluated by a (calibrated) model when needed.
`decidable_from` is `E0` (targetable in v1) or `agent_runtime` (deferred).

### Model's role (5a)
One model call: given signals A, B, and the env diff, output `{condition,
grounding{kind,check,decidable_from}, value_space}`. Strict JSON, validated. The
model is told to prefer an executable `check` and to set `decidable_from="E0"` only
if the check is decidable from initial setup alone.

---

## 5b. Candidate Synthesizer (negate / mutate)

### Input
A `Guard` + a frontier item carrying **all `observed_siblings`** at that branch (NL
descriptions) and a `task`:
- **Negate** (binary guard): drive it to the other value ("the test fails with an
  assertion error" → "the test does not fail" / "fails some other way").
- **Novel sibling** (multivalued / multi-edge branch): because a branch often already
  has several siblings, the synthesizer is given **all of them** and asked for a
  **new, diverse** condition **distinct from every observed sibling** (e.g. observed
  {import error, assertion error} → propose "the test times out" or "collection
  errors before any test runs"). This explores breadth at high-fan-out nodes instead
  of re-hitting a known region ([D-TREE-2](./tree-builder.md#branch-frontier--selection-priority)).

All conditions are **natural-language** (not enum tokens).

### Output
A [`Candidate`](../data-contracts.md#1-candidate--an-input-to-the-runner) `(x*, E_0*)`
with `provenance.mutation` filled as **NL** — `{guard_id, op, instruction, from, to}`
(e.g. `instruction:"make the failing test fail with an import error instead of an
assertion error"`) — so the eventual bug report can cite the mutated assumption.

### Model's role (5b)
One model call: a **generator** drafts `(x*, E_0*)` — a prompt plus a **Containerfile
tail** (the writable region under the pinned `FROM <base_image>` prefix,
[environments.md](../environments.md#per-test-containerfile-generated-fast)) intended
to satisfy the **path into the branch** *and* the **mutated guard**. The generator
writes only the tail; the fixed prefix and structure rule are enforced at validation.
The generator "may be dumb": its output is a *candidate*, not trusted. Correctness is
enforced downstream by 5c.

---

## 5c. Validator (the key efficiency move — a separate unit)

> **"LLM proposes, checker disposes."** A botched candidate costs a retry, never an
> agent run.

### Input
A `Candidate` + the `Guard` it targets.

### Behavior
`validate(container, guard) -> ValidationReport` — a unit defined over a **container
instance**, with **no agent**:
1. **Structure check (before any build):** the candidate's Containerfile begins with
   the exact pinned `FROM <base_image>`, has a single `FROM`, writes only inside the
   tail markers, and embeds no secret ([environments.md](../environments.md#enforcing-the-structure-rule)).
   Fail ⇒ reject, no build.
2. If `guard.grounding.decidable_from != "E0"` → **defer**: return
   `decidable:false`, do not build, do not run. (Deferred guards are *counted*, not
   targeted in v1.)
3. Otherwise run the guard's plain `check` **in the built container, with no agent**
   — e.g. does `pytest -q` exit non-zero with an `ImportError`, as the mutation
   requires? Plus sanity checks (workdir exists; path-into-branch precondition holds
   where checkable from `E_0`).

**Where the container comes from.** In the production loop these checks run in the
**run's own container as the gate phase before the agent** — one build serves both
validation and the run, and the agent runs in exactly the approved instance
([per-run flow](../environments.md#per-run-flow-validate-then-run-in-the-same-container)).
Run standalone (or via the Runner's `--dry-run`), `validate` builds a throwaway
container, checks, and destroys it. Either way **no agent is launched**, and the
Containerfile build hits the per-skill base cache so only the cheap tail rebuilds.

### Output
A [`ValidationReport`](../data-contracts.md#9-candidate-synthesizer--validationreport):

```json
{ "candidate_id":"cand-…","guard_id":"g1","valid":true,"decidable":true,
  "checks":[
    {"name":"container_builds","ok":true,"detail":"image built in 31s"},
    {"name":"guard_holds_in_setup","ok":true,"command":"pytest -q 2>&1 | grep -q ImportError","exit_code":0,"detail":"setup fails with ImportError as required"}],
  "rejected_reason": null }
```

Only a `valid:true` gate lets the agent run proceed (the agent never starts in a
container the validator rejected; an invalid candidate is destroyed before any agent
run, costing only a build + checks).

---

## Dependencies

**Needs:**
- The **judgment model** via a **direct provider API call** (not Pi) for 5a
  (extraction) and 5b (generation). Temperature 0, cached.
- **Docker** for 5c (Containerfile build + in-container checks). 5c's checks run
  against a `ContainerHandle` provided by the Runner's lifecycle (the gate phase) or
  built standalone for testing — one container-build implementation, shared with the
  Runner ([environments.md](../environments.md#how-this-preserves-composability)).

**Does NOT depend on:**
- The **agent under test / Pi** — the entire point of 5c is to validate *without*
  an agent run.
- The property checker. (Guards and properties are independent; a guard targets
  *coverage*, a property judges *correctness*.)
- The raw trace — it works from `Episode`s and the tree.

---

## How to test it in isolation

### 5a — extraction (recorded model)
Fixtures `tests/fixtures/guards/extract/`:
- `import_vs_assertion/` — two diverging episodes (import error vs assertion
  failure); assert the guard's `condition` mentions the import/assertion
  distinction, `grounding.kind="executable"`, `decidable_from="E0"`,
  `value_space.type="multivalued"`.
- `outcome_reasoning_disagree/` — observation says "1 failed" but next reasoning
  says "passed, moving on"; assert the extractor emits the **disagreement bug
  signal** (a flag the property checker consumes), distinct from a normal guard.
- `tactics_not_used/` — two episodes that reached the same outcome via different
  tool calls; assert the guard does **not** reference the internal tool calls.

### 5b — synthesis (recorded model)
- `negate_binary/` — binary guard ⇒ candidate targets the opposite value;
  `provenance.mutation.op="negate"`, NL `instruction`/`from`/`to`.
- `novel_sibling/` — branch with `observed_siblings=["import error","assertion
  error"]` ⇒ the candidate targets a condition **distinct from both** (e.g. "the test
  times out"); assert `provenance.mutation.op="novel_sibling"` and that `to` matches
  none of the observed siblings (a diversity check over the NL conditions).
- `containerfile_tail_only/` — assert the generated Containerfile is `FROM
  <base_image>` + tail-only (the synthesizer never touches the prefix).

### 5c — validator (the most important; plain code + Docker, no model, no agent)
Fixtures `tests/fixtures/guards/validate/`:
- `valid_setup_decidable/` — a Containerfile whose tail genuinely makes the test
  fail with an ImportError; assert `valid:true`, the `guard_holds_in_setup` check ran
  in the built container and passed, **and assert no agent/Pi process was spawned**
  (mock the agent entry point and assert it was never called — the efficiency
  guarantee made into a test).
- `invalid_setup/` — a Containerfile tail that does **not** reproduce the import
  error; assert `valid:false`, `rejected_reason` set, no agent run, retry signaled.
- `bad_structure/` — Containerfiles that omit the pinned `FROM`, add a second `FROM`,
  write outside the tail markers, or embed a secret; assert `valid:false` at the
  `containerfile_structure_ok` check **with no build attempted**.
- `build_fails/` — a broken tail; assert `valid:false` with the build log, no agent
  run.
- `deferred_runtime_guard/` — a guard with `decidable_from="agent_runtime"`; assert
  `decidable:false`, **no build**, counted as deferred.

### Test shape (the efficiency invariant)

```python
def test_validator_never_spends_an_agent_run(monkeypatch):
    spy = monkeypatch.setattr(runner, "run_agent", fail_if_called)
    rep = validate(load_candidate("valid_setup_decidable"), load_guard("g1"))
    assert rep.valid and rep.decidable
    assert any(c.name == "guard_holds_in_setup" and c.ok for c in rep.checks)
    # fail_if_called raises if the agent was ever launched
```

---

## Failure modes

| Situation | Behavior |
|-----------|----------|
| 5a model returns a guard with no executable grounding | Allowed: `grounding.kind="natural_language"`; such guards are only *targeted* if `decidable_from="E0"` is still claimed and checkable, else deferred. NL guards are evaluated by the calibrated model when needed. |
| 5a marks a runtime-only guard as `E0` wrongly | Caught by 5c: the `guard_holds_in_setup` check has nothing to verify from setup → flagged invalid/deferred; never silently run. |
| 5b generates an invalid candidate | **Expected and cheap**: 5c rejects it (`valid:false`), the loop retries the generator up to a cap, then abandons the branch and counts it. No agent run wasted. |
| Container build flake (network) | Transport retry in 5c; if persistent, `valid:false` with build log; the branch is requeued. |
| Targeted guard is `agent_runtime` | Deferred and **counted** (tex v1 scope); reported as deferred-guard count, not silently dropped. |
| Outcome/reasoning disagreement detected | Emitted as a bug signal to Component 6 **in addition to** (or instead of) a normal guard — a divergence is a finding, not an error to reconcile. |

**Surfacing:** the validator's verdict and every check it ran are written to the
`ValidationReport`, shipped with the candidate. Rejections, deferrals, and
disagreements are all counted in campaign stats — the system reports *why* it did or
did not spend each agent run.
