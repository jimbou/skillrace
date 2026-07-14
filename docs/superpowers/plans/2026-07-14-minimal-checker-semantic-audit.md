# Minimal Checker Semantic Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one bounded pre-run semantic self-audit over every candidate's generated property checkers, repair each rejected checker at most once, and stop unjudgeable candidates before agent execution.

**Architecture:** Extend the existing `skillrace.compile_checks` flow in place: author and mechanically validate scripts, send all scripts through one same-model JSON audit call, rewrite only rejected scripts, then publish the existing manifest with audit identity and accounting. Keep the current journal, campaign interface, and three-method execution path unchanged. After implementation, audit the complete RQ1/RQ3 property surface and review the wider pipeline for removable complexity before any paid run.

**Tech Stack:** Python 3.12, pytest, Bash checker scripts, existing Yunwu `chat` client and journal.

---

### Task 1: Define and validate the small audit contract

**Files:**
- Modify: `skillrace/compile_checks.py`
- Create: `tests/test_checker_semantic_audit.py`

- [ ] **Step 1: Write failing response-contract tests**

Add tests for the exact accepted JSON shape and fail-closed malformed cases:

```python
def test_parse_semantic_audit_requires_one_decision_per_property():
    value = compiler.parse_semantic_audit(
        '{"checks": ['
        '{"property_id":"p1","decision":"accept","reason":"supported"},'
        '{"property_id":"p2","decision":"reject","reason":"guessed signature"}'
        ']}',
        ["p1", "p2"],
    )
    assert value == [
        {"property_id": "p1", "decision": "accept", "reason": "supported"},
        {"property_id": "p2", "decision": "reject", "reason": "guessed signature"},
    ]


@pytest.mark.parametrize("content", [
    "not json",
    '{"checks": []}',
    '{"checks": [{"property_id":"p1","decision":"maybe","reason":"x"}]}',
    '{"checks": ['
    '{"property_id":"p1","decision":"accept","reason":"x"},'
    '{"property_id":"p1","decision":"accept","reason":"x"}'
    ']}',
])
def test_parse_semantic_audit_fails_closed_on_malformed_or_incomplete_output(content):
    with pytest.raises(ValueError, match="semantic audit"):
        compiler.parse_semantic_audit(content, ["p1", "p2"])
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_checker_semantic_audit.py -k parse_semantic_audit
```

Expected: collection or assertion failure because `parse_semantic_audit` does not exist.

- [ ] **Step 3: Implement the minimal parser and version constants**

Add to `skillrace/compile_checks.py`:

```python
SEMANTIC_AUDIT_PROMPT_VERSION = "checker-semantic-audit-v1"
SEMANTIC_REWRITE_PROMPT_VERSION = "checker-semantic-rewrite-v1"
SEMANTIC_AUDIT_POLICY_VERSION = "pre-run-five-rules-v1"


def parse_semantic_audit(content: str, property_ids: list[str]) -> list[dict]:
    raw = content.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("semantic audit response is not JSON") from error
    rows = value.get("checks") if isinstance(value, dict) else None
    if not isinstance(rows, list) or len(rows) != len(property_ids):
        raise ValueError("semantic audit must decide every property exactly once")
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "property_id", "decision", "reason"
        }:
            raise ValueError("semantic audit decision has invalid fields")
        property_id = row["property_id"]
        decision = row["decision"]
        reason = row["reason"]
        if (
            not isinstance(property_id, str)
            or decision not in {"accept", "reject"}
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 500
        ):
            raise ValueError("semantic audit decision is invalid")
        normalized.append({
            "property_id": property_id,
            "decision": decision,
            "reason": reason.strip(),
        })
    if [row["property_id"] for row in normalized] != property_ids:
        raise ValueError("semantic audit property IDs are missing, duplicated, or reordered")
    return normalized
```

- [ ] **Step 4: Verify GREEN**

Run the Step 2 command. Expected: all parser-contract tests pass.

### Task 2: Reproduce both saved checker failures through one batch audit

**Files:**
- Modify: `skillrace/compile_checks.py`
- Modify: `tests/test_checker_semantic_audit.py`

- [ ] **Step 1: Add failing prompt and orchestration regressions**

Embed small representatives of the saved failures rather than depending on development
output directories:

```python
DATAFRAME_PROMPT = (
    "Parse sensor_data.json and flatten it into a clean pandas DataFrame with one "
    "row per reading."
)
JSON_PROPERTIES = [
    {
        "id": "valid-json-out",
        "reads": "state",
        "nl": "IF the parser emits JSON, the output is syntactically valid JSON.",
    },
    {
        "id": "parses-valid",
        "reads": "state",
        "nl": "The parser accepts valid prompt inputs and produces the expected structure.",
    },
]

BAD_JSON_CHECK = """#!/usr/bin/env bash
main_parser=$(find /workspace -name '*.py' | head -1)
[ -z "$main_parser" ] && exit 0
python3 "$main_parser" /tmp/input.json > /tmp/output || true
# Invalid fallback: manufacture the expected output.
python3 -c 'import json; print(json.dumps(json.load(open("/tmp/input.json"))))' > /tmp/output
python3 -c 'import json; json.load(open("/tmp/output"))'
"""

BAD_CALLABLE_CHECK = """#!/usr/bin/env bash
script_path=$(find /workspace -name '*.py' | head -1)
[ -z "$script_path" ] && exit 0
python3 - "$script_path" <<'PY'
for name in ['parse_data', 'process_json', 'main', 'process']:
    if hasattr(module, name):
        parser_func = getattr(module, name)
        break
result = parser_func(data)
PY
"""


def fake_audit_chat(captured, decisions):
    def fake(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        usage = {
            "prompt_tokens": 101,
            "completion_tokens": 23,
            "total_tokens": 124,
            "cached_input_tokens": 7,
        }
        return {
            "content": json.dumps({
                "checks": [
                    {"property_id": pid, "decision": decision, "reason": reason}
                    for pid, decision, reason in decisions
                ]
            }),
            "usage": usage,
            "cost_provider_credits": 0.04,
            "model": kwargs["model"],
            "operation_id": "audit-op-1",
            "journal_terminal_receipt": {"usage": usage},
            "journal_terminal_receipt_sha256": "a" * 64,
            "journal_call_terminal_receipt_sha256": "b" * 64,
        }
    return fake


def test_audit_prompt_contains_task_properties_all_scripts_and_five_rejection_rules(
    monkeypatch,
):
    captured = {}
    monkeypatch.setattr(compiler, "chat", fake_audit_chat(captured, decisions=[
        ("valid-json-out", "reject", "unconditional JSON and manufactured output"),
        ("parses-valid", "reject", "guessed callable and signature"),
    ]))
    decisions, _, _ = compiler.audit_checks(
        properties=JSON_PROPERTIES,
        prompt=DATAFRAME_PROMPT,
        skill="json-parser",
        tools=["bash", "python3", "find"],
        tree=["sensor_data.json"],
        scripts={
            "valid-json-out": BAD_JSON_CHECK,
            "parses-valid": BAD_CALLABLE_CHECK,
        },
        model="model-a",
    )
    text = json.dumps(captured["messages"])
    assert DATAFRAME_PROMPT in text
    assert BAD_JSON_CHECK in text and BAD_CALLABLE_CHECK in text
    for phrase in (
        "unsupported by the task prompt",
        "callable signatures",
        "conditional",
        "missing required artifacts",
        "manufacture or echo",
    ):
        assert phrase in text
    assert [row["decision"] for row in decisions] == ["reject", "reject"]
```

The fake chat result must include realistic `usage`, cost, operation ID, and redacted
terminal receipt hashes so later accounting assertions exercise the same shape as the
real client.

- [ ] **Step 2: Run the regressions and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_checker_semantic_audit.py -k 'audit_prompt or saved_checker'
```

Expected: FAIL because `audit_checks` and its fixed prompt do not exist.

- [ ] **Step 3: Implement one audit call over all scripts**

Add a fixed `SEMANTIC_AUDIT_SYS` containing only the five rejection rules, the pre-run
boundary, and the exact JSON output contract. Implement:

```python
def audit_checks(*, properties, prompt, skill, tools, tree, scripts, model):
    payload = {
        "task_prompt": prompt,
        "properties": properties,
        "initial_tools": tools,
        "initial_tree": tree[:80],
        "scripts": [
            {"property_id": prop["id"], "script": scripts[prop["id"]]}
            for prop in properties
        ],
    }
    response = chat(
        [
            {"role": "system", "content": SEMANTIC_AUDIT_SYS},
            {"role": "user", "content": json.dumps(payload, indent=2)},
        ],
        model=model,
        temperature=0.0,
        reasoning=False,
        max_tokens=1600,
        tag="compile.check.audit",
        skill=skill,
    )
    decisions = parse_semantic_audit(
        response["content"], [prop["id"] for prop in properties]
    )
    return decisions, float(response["cost_provider_credits"]), model_call_summary(response)
```

Implement `model_call_summary` as a small redacted extractor containing phase-neutral
operation identity, model, input/output/cache-read token integers, provider-credit cost,
and the two terminal receipt hashes. Do not add another receipt file format.

```python
def model_call_summary(response: dict) -> dict:
    terminal = response.get("journal_terminal_receipt")
    usage = terminal.get("usage") if isinstance(terminal, dict) else None
    if not isinstance(usage, dict):
        usage = response.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("model call receipt lacks usage")
    cache_read = usage.get("cached_input_tokens", 0)
    if isinstance(cache_read, bool) or not isinstance(cache_read, int) or cache_read < 0:
        raise ValueError("model call cache-read usage is invalid")
    return {
        "operation_id": response["operation_id"],
        "model": response["model"],
        "input_tokens": usage["prompt_tokens"],
        "output_tokens": usage["completion_tokens"],
        "cache_read_tokens": cache_read,
        "cost_provider_credits": float(response["cost_provider_credits"]),
        "terminal_receipt_sha256": response["journal_terminal_receipt_sha256"],
        "call_terminal_receipt_sha256": response[
            "journal_call_terminal_receipt_sha256"
        ],
    }
```

- [ ] **Step 4: Verify GREEN**

Run the Step 2 command. Expected: the exact json-parser representatives are present in
one audit request and both rejection decisions parse successfully.

### Task 3: Integrate one targeted rewrite and fail closed before the agent

**Files:**
- Modify: `skillrace/compile_checks.py`
- Modify: `tests/test_checker_semantic_audit.py`
- Modify: `tests/test_check_isolation.py`

- [ ] **Step 1: Write the failing end-to-end compiler test**

Create a temporary candidate with the DataFrame prompt, make `author_check` return the
two bad scripts, make `audit_checks` reject both, and make `rewrite_semantic_check`
return corrected scripts. Assert:

```python
assert audit_calls == [["valid-json-out", "parses-valid"]]
assert rewrite_calls == ["valid-json-out", "parses-valid"]
assert manifest["semantic_audit"]["status"] == "accepted-after-rewrite"
assert [row["rewritten"] for row in manifest["checks"]] == [True, True]
assert all(row["syntax_ok"] and row["policy_ok"] for row in manifest["checks"])
```

Add a second test where the rewrite has invalid Bash syntax and assert
`compiler.compile_case(case, JSON_PROPERTIES, "model-a", image="candidate:built")`
raises `RuntimeError` containing `semantic rewrite invalid`. Assert the rewrite function
was called exactly once for that property.

Add a third test where initial authoring and the existing mechanical correction both
remain invalid. Assert compilation raises `RuntimeError` containing
`checker mechanically invalid` before `audit_checks` is called.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_checker_semantic_audit.py -k 'compile_case or rewrite'
```

Expected: FAIL because semantic decisions are not integrated into `compile_case`.

- [ ] **Step 3: Implement the bounded rewrite path**

Add `rewrite_semantic_check`, using a fixed rewrite prompt with the task, one
property, rejected script, and audit reason. In `compile_case`, after existing authoring
and mechanical validation:

```python
SEMANTIC_REWRITE_SYS = (
    "Rewrite one pre-run Bash property checker to fix the supplied semantic-audit "
    "rejection. Test only requirements supported by the task and property. Do not "
    "guess callable signatures, turn absent conditional preconditions into failures, "
    "treat missing required artifacts as success, or manufacture expected output. "
    "Output only the complete Bash script."
)


def rewrite_semantic_check(
    *, prop, skill, prompt, tools, tree, model, script, reason
):
    payload = {
        "task_prompt": prompt,
        "property": prop,
        "initial_tools": tools,
        "initial_tree": tree[:80],
        "rejection_reason": reason,
        "rejected_script": script,
    }
    response = chat(
        [
            {"role": "system", "content": SEMANTIC_REWRITE_SYS},
            {"role": "user", "content": json.dumps(payload, indent=2)},
        ],
        model=model,
        temperature=0.0,
        reasoning=False,
        max_tokens=1600,
        tag="compile.check.rewrite",
        skill=skill,
    )
    return (
        _strip_fences(response["content"]),
        float(response["cost_provider_credits"]),
        model_call_summary(response),
    )
```

Then integrate the calls:

```python
property_by_id = {prop["id"]: prop for prop in props}
scripts = {
    entry["property_id"]: (checks_dir / entry["script"]).read_text()
    for entry in entries
}
invalid = [entry for entry in entries if not entry["syntax_ok"] or not entry["policy_ok"]]
if invalid:
    details = "; ".join(
        f"{entry['property_id']}: {entry['error']}" for entry in invalid
    )
    raise RuntimeError(f"checker mechanically invalid: {details}")
decisions, audit_cost, audit_call = audit_checks(
    properties=props,
    prompt=cand["prompt"],
    skill=cand.get("skill"),
    tools=tools,
    tree=tree,
    scripts=scripts,
    model=model,
)
cost += audit_cost
for entry, decision in zip(entries, decisions, strict=True):
    entry["initial_sha256"] = entry["sha256"]
    entry["audit_decision"] = decision["decision"]
    entry["audit_reason"] = decision["reason"]
    entry["rewritten"] = False
    if decision["decision"] == "reject":
        rewritten, rewrite_cost, rewrite_call = rewrite_semantic_check(
            prop=property_by_id[entry["property_id"]],
            skill=cand.get("skill"),
            prompt=cand["prompt"],
            tools=tools,
            tree=tree,
            model=model,
            script=scripts[entry["property_id"]],
            reason=decision["reason"],
        )
        cost += rewrite_cost
        atomic_write_text(checks_dir / entry["script"], rewritten)
        syntax_ok, syntax_error = _syntax_ok(checks_dir / entry["script"])
        policy_ok, policy_error = validate_script_policy(
            rewritten, tools, reads=property_by_id[entry["property_id"]].get("reads")
        )
        if not syntax_ok or not policy_ok:
            raise RuntimeError(
                f"semantic rewrite invalid for {entry['property_id']}: "
                f"{syntax_error or policy_error}"
            )
        entry.update(
            rewritten=True,
            sha256=file_hash(checks_dir / entry["script"]),
            syntax_ok=True,
            policy_ok=True,
            error=None,
        )
```

Store the audit summary and rewrite call summaries in the normal manifest. Do not modify
the campaign engine: its existing exception handling already returns pre-agent
`compile_error`, which consumes no agent budget.

- [ ] **Step 4: Verify GREEN and existing checker behavior**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_checker_semantic_audit.py tests/test_check_isolation.py
```

Expected: all focused tests pass, including the existing syntax/policy repair tests.

### Task 4: Bind audit identity, tokens, receipts, and cost to the manifest

**Files:**
- Modify: `skillrace/compile_checks.py`
- Modify: `tests/test_compile_identity.py`
- Modify: `tests/test_checker_semantic_audit.py`

- [ ] **Step 1: Write failing identity and accounting tests**

Require `compile_fingerprint` to change when any semantic audit/rewrite/policy version
changes. Require a successful manifest to contain:

```python
assert manifest["semantic_audit"]["prompt_version"] == compiler.SEMANTIC_AUDIT_PROMPT_VERSION
assert manifest["semantic_audit"]["policy_version"] == compiler.SEMANTIC_AUDIT_POLICY_VERSION
assert manifest["semantic_audit"]["call"]["input_tokens"] == 101
assert manifest["semantic_audit"]["call"]["output_tokens"] == 23
assert manifest["semantic_audit"]["call"]["cache_read_tokens"] == 7
assert manifest["semantic_audit"]["cost_provider_credits"] == pytest.approx(0.04)
assert manifest["cost_provider_credits"] == pytest.approx(0.10 + 0.04 + 0.02)
```

Also assert a matching fingerprint and matching final script hashes reuse the manifest
without probing, authoring, auditing, or rewriting.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_compile_identity.py tests/test_checker_semantic_audit.py -k 'fingerprint or accounting or cache'
```

Expected: FAIL because semantic audit versions/accounting are absent.

- [ ] **Step 3: Extend the existing identity and manifest only**

Add the three semantic version constants to the object hashed by
`compile_fingerprint`. Keep the return signature `(manifest, total_cost)` unchanged.
Add audit/rewrite model-call summaries under `manifest["semantic_audit"]`; do not add a
new ledger. Ensure `manifest["cost_provider_credits"]` and the returned cost include all
author, mechanical repair, audit, and semantic rewrite calls.

- [ ] **Step 4: Verify GREEN**

Run the Step 2 command. Expected: identity, accounting, and cache tests pass.

### Task 5: Audit the complete property and saved-checker surface offline

**Files:**
- Create: `docs/2026-07-14-checker-suite-audit.md`
- Modify only if unsupported requirements are found: `skills/*/properties.json`
- Modify only if unsupported requirements are found: `scenarios/*/campaign/properties.json`

- [ ] **Step 1: Verify suite inventory and schema without model calls**

Run a repository-local Python command that loads exactly the 30 manifest-listed RQ1
property files and 10 RQ3 campaign property files, verifies unique nonempty IDs, supported
`reads` values, and records counts. Expected inventory: 30 RQ1 skills and 10 RQ3
scenarios; any mismatch is a failing audit result.

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

manifest = json.loads(Path("experiments/manifests/rq1-skills.draft.json").read_text())
skills = [row["id"] for row in manifest["headline_skills"]]
scenarios = sorted(Path("scenarios").glob("*/campaign/properties.json"))
assert len(skills) == 30, len(skills)
assert len(scenarios) == 10, len(scenarios)
for label, path in [
    *((skill, Path("skills") / skill / "properties.json") for skill in skills),
    *((path.parts[1], path) for path in scenarios),
]:
    rows = json.loads(path.read_text())
    ids = [row.get("id") for row in rows]
    assert ids and all(isinstance(value, str) and value for value in ids), label
    assert len(ids) == len(set(ids)), label
    assert all(row.get("reads") in {"state", "trace", "state+trace"} for row in rows), label
print(f"RQ1 skills={len(skills)} RQ3 scenarios={len(scenarios)}")
PY
```

- [ ] **Step 2: Review every property specification**

Record each property ID in the audit document and classify conditional preconditions,
required artifacts, task-dependent interfaces, and potentially unsupported requirements.
Change a property specification only when its requirement cannot be justified by the
skill or generated task contract; do not tune it to any observed method outcome.

- [ ] **Step 3: Inventory all saved generated scripts**

Use `find` plus `rg` over `out/development-pilots`, `experiments/development-pilots`, and
scenario checker artifacts to count and inspect:

```text
missing artifact followed by exit 0
guessed callable-name lists
stdout parsed as JSON
fallbacks that print/copy test input
conditional properties without a precondition branch
```

Record both raw pattern counts and manually confirmed invalid examples. Clearly label
raw matches as triage rather than proven defects.

- [ ] **Step 4: Run the complete offline checker-focused tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_checker_semantic_audit.py tests/test_compile_identity.py tests/test_check_isolation.py tests/test_campaign_engine.py tests/test_rq3_campaign_adapter.py
```

Expected: zero failures. Do not make a paid call if the inventory, review, or tests fail.

### Task 6: Review the end-to-end pipeline for simplification

**Files:**
- Create: `docs/2026-07-14-pipeline-simplification-review.md`

- [ ] **Step 1: Trace the actual headline path**

Follow code and artifacts through candidate generation, realization/build repair, sanity,
checker compilation, agent execution, isolated checking, grouping, unchanged-skill
confirmation, patch-only repair, exact replay, verified analysis, and documentation.
Ignore historical plans that are not invoked by the current draft protocols.

- [ ] **Step 2: Classify each gate and durable artifact**

For each step, record its purpose and classify it as `keep`, `simplify`, or `remove`.
Use these decision rules:

```text
keep      protects fairness, agent-budget accounting, exact replay, or final totals
simplify  serves a required purpose but duplicates data/calls or has avoidable layers
remove    historical/debug-only or unused by the paper's current execution path
```

- [ ] **Step 3: Produce a short prioritized deletion/collapse list**

List no more than ten changes, ordered by reduction in moving parts versus risk. Do not
implement broad pipeline changes in the checker patch. Any later behavior change gets a
separate small design and regression test.

### Task 7: Synchronize documentation and run full offline verification

**Files:**
- Modify: `docs/property-checker.md`
- Modify: `docs/design/property-checker.md`
- Modify: `docs/data-contracts.md`
- Modify: `STATUS.md`
- Modify: `docs/implementation-status.md`
- Modify: `handoff.md`
- Modify: `docs/2026-07-14-session-handoff.md`

- [ ] **Step 1: Document only the implemented minimal behavior**

Replace `compile-check-v3` descriptions where necessary with the new author-plus-audit
flow, state that it is a same-model self-audit, document one rewrite and fail-closed
pre-agent behavior, and include manifest identity/accounting fields.

- [ ] **Step 2: Run focused verification**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_checker_semantic_audit.py tests/test_compile_identity.py tests/test_check_isolation.py tests/test_campaign_engine.py
```

Expected: zero failures.

- [ ] **Step 3: Run the complete no-live suite and static checks**

Run:

```bash
.venv/bin/python -m pytest -m 'not live'
.venv/bin/python -m compileall -q skillrace tests
git diff --check
```

Expected: all commands exit zero. If any fail, diagnose and fix before considering a
paid operation.

- [ ] **Step 4: Update both handoffs with exact evidence**

Record commands, test counts, audit findings, simplification recommendations, failures,
and the remaining bounded live gate. Do not claim checker correctness beyond the tests
and review actually performed.

### Task 8: Run one fresh bounded patch/replay chain only after the offline gate

**Files:**
- Create: a new uniquely named directory under `out/development-pilots/2026-07-14/`
- Modify: `handoff.md`
- Modify: `docs/2026-07-14-session-handoff.md`

- [ ] **Step 1: Select one different saved failure manually**

Exclude the json-parser v4 failure and every prior terminal operation identity. Require
the task prompt to support the failed property, the frozen checker to invoke the actual
artifact interface correctly, required artifacts to fail when absent, and no manufactured
expected output. Record the selected source hashes before launching anything.

- [ ] **Step 2: Preflight the exact bounded chain without executing it**

Resolve one fresh output root and operation identities, confirm no receipt already uses
them, confirm the saved campaign/skill/checker hashes, and estimate the bounded call
budget. Stop if any identity or evidence is ambiguous.

- [ ] **Step 3: Execute exactly one chain**

Invoke the existing patch-only backend once, then the existing independent exact replay
once, then the strict RQ1 cell verifier once. Do not rerun a timeout, error, unknown, or
same-failure terminal. Preserve the immutable receipts produced by those existing
components.

- [ ] **Step 4: Record the result conservatively**

Count a confirmed defect only if the replay passes every property that originally
failed. Record input, output, cache-read tokens, provider credits, wall time, and all
terminal statuses regardless of outcome. Update both handoffs before stopping.

---

## Execution notes

- Work in the existing workspace because the user explicitly requested continuation
  from its intentional uncommitted state; never reset, clean, or overwrite unrelated
  changes.
- Stage and commit only files created or intentionally modified by this plan.
- No subagents are used unless the user explicitly requests delegation.
- No paid call occurs before Tasks 1–7 pass and the checker-suite audit is reviewed.
