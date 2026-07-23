from __future__ import annotations

import json
from pathlib import Path

import pytest

import skillrace.compile_checks as compiler


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
            "content": json.dumps(
                {
                    "checks": [
                        {
                            "property_id": property_id,
                            "decision": decision,
                            "reason": reason,
                        }
                        for property_id, decision, reason in decisions
                    ]
                }
            ),
            "usage": usage,
            "cost_provider_credits": 0.04,
            "model": kwargs["model"],
            "operation_id": "audit-op-1",
            "journal_terminal_receipt": {"usage": usage},
            "journal_terminal_receipt_sha256": "a" * 64,
            "journal_call_terminal_receipt_sha256": "b" * 64,
        }

    return fake


def _write_case(tmp_path: Path) -> Path:
    case = tmp_path / "case"
    case.mkdir()
    candidate = {
        "candidate_id": "sensor-case",
        "prompt": DATAFRAME_PROMPT,
        "containerfile": "FROM base@sha256:one\n",
        "base_image": "base@sha256:one",
        "skill": "json-parser",
    }
    (case / "candidate.json").write_text(json.dumps(candidate))
    (case / "Dockerfile").write_text(candidate["containerfile"])
    return case


def _patch_case_probe(monkeypatch):
    monkeypatch.setattr(
        compiler, "inspect_image_digest", lambda _image: "sha256:candidate"
    )
    monkeypatch.setattr(
        compiler,
        "probe_initial_env",
        lambda _image: (["bash", "python3", "find"], ["sensor_data.json"]),
    )


def _call_summary(operation_id: str, cost: float) -> dict:
    return {
        "operation_id": operation_id,
        "model": "model-a",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cost_provider_credits": cost,
        "terminal_receipt_sha256": "c" * 64,
        "call_terminal_receipt_sha256": "d" * 64,
    }


def test_model_call_summary_preserves_unknown_development_price():
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cached_input_tokens": 0,
    }
    response = {
        "operation_id": "unpriced-op",
        "model": "glm-4.7",
        "usage": usage,
        "cost_provider_credits": None,
        "journal_terminal_receipt": {"usage": usage},
        "journal_terminal_receipt_sha256": "a" * 64,
        "journal_call_terminal_receipt_sha256": "b" * 64,
    }

    summary = compiler.model_call_summary(response)

    assert summary["cost_provider_credits"] is None
    assert summary["cost_accounting"] == "unknown-nonzero-possible"


def test_author_result_unpacker_treats_unknown_price_as_unpriced_not_failure():
    script, known_cost, call = compiler._unpack_author_result(
        ("#!/usr/bin/env bash\nexit 0\n", None, {"operation_id": "op"})
    )

    assert script.startswith("#!/usr/bin/env bash")
    assert known_cost == 0.0
    assert call == {"operation_id": "op"}


def test_bash_heredoc_warning_is_mechanical_invalidity(tmp_path):
    script = tmp_path / "broken.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "python3 <<'EOF'\n"
        "print('ok')\n"
        "EOF argument-after-terminator\n"
    )

    valid, error = compiler._syntax_ok(script)

    assert valid is False
    assert "here-document" in error


def test_parse_semantic_audit_requires_one_decision_per_property():
    value = compiler.parse_semantic_audit(
        '{"checks": ['
        '{"property_id":"p1","decision":"accept","reason":"supported"},'
        '{"property_id":"p2","decision":"reject","reason":"guessed signature"}'
        "]}",
        ["p1", "p2"],
    )

    assert value == [
        {"property_id": "p1", "decision": "accept", "reason": "supported"},
        {"property_id": "p2", "decision": "reject", "reason": "guessed signature"},
    ]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"checks": []}',
        '{"checks": [{"property_id":"p1","decision":"maybe","reason":"x"}]}',
        '{"checks": ['
        '{"property_id":"p1","decision":"accept","reason":"x"},'
        '{"property_id":"p1","decision":"accept","reason":"x"}'
        "]}",
    ],
)
def test_parse_semantic_audit_fails_closed_on_malformed_or_incomplete_output(content):
    with pytest.raises(ValueError, match="semantic audit"):
        compiler.parse_semantic_audit(content, ["p1", "p2"])


def test_audit_prompt_contains_both_saved_checker_failures_and_five_rules(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        compiler,
        "chat",
        fake_audit_chat(
            captured,
            decisions=[
                (
                    "valid-json-out",
                    "reject",
                    "unconditional JSON requirement and manufactured output",
                ),
                ("parses-valid", "reject", "guessed callable and signature"),
            ],
        ),
    )

    decisions, cost, call = compiler.audit_checks(
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

    payload = json.loads(captured["messages"][1]["content"])
    assert payload["task_prompt"] == DATAFRAME_PROMPT
    assert [row["script"] for row in payload["scripts"]] == [
        BAD_JSON_CHECK,
        BAD_CALLABLE_CHECK,
    ]
    system_prompt = captured["messages"][0]["content"]
    for phrase in (
        "unsupported by the task prompt",
        "callable signatures",
        "conditional",
        "missing required artifacts",
        "manufacture or echo",
    ):
        assert phrase in system_prompt
    assert captured["kwargs"]["tag"] == "compile.check.audit"
    assert [row["decision"] for row in decisions] == ["reject", "reject"]
    assert cost == pytest.approx(0.04)
    assert call == {
        "operation_id": "audit-op-1",
        "model": "model-a",
        "input_tokens": 101,
        "output_tokens": 23,
        "cache_read_tokens": 7,
        "cost_provider_credits": 0.04,
        "terminal_receipt_sha256": "a" * 64,
        "call_terminal_receipt_sha256": "b" * 64,
    }


def test_compile_case_audits_both_saved_failures_once_and_excludes_them(
    tmp_path, monkeypatch
):
    case = _write_case(tmp_path)
    _patch_case_probe(monkeypatch)
    authored = iter([(BAD_JSON_CHECK, 0.1), (BAD_CALLABLE_CHECK, 0.1)])
    monkeypatch.setattr(compiler, "author_check", lambda *args, **kwargs: next(authored))
    audit_calls = []

    def fake_audit(**kwargs):
        audit_calls.append(list(kwargs["scripts"]))
        return (
            [
                {
                    "property_id": "valid-json-out",
                    "decision": "reject",
                    "reason": "unconditional JSON and manufactured output",
                },
                {
                    "property_id": "parses-valid",
                    "decision": "reject",
                    "reason": "guessed callable and signature",
                },
            ],
            0.04,
            _call_summary("audit-op", 0.04),
        )

    monkeypatch.setattr(compiler, "audit_checks", fake_audit)
    rewrite_calls = []

    monkeypatch.setattr(
        compiler,
        "rewrite_semantic_check",
        lambda **kwargs: rewrite_calls.append(kwargs["prop"]["id"]),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="no usable property checkers"):
        compiler.compile_case(
            case, JSON_PROPERTIES, "model-a", image="candidate:built"
        )

    assert audit_calls == [["valid-json-out", "parses-valid"]]
    assert rewrite_calls == []


def test_compile_case_does_not_rewrite_a_semantic_rejection(
    tmp_path, monkeypatch
):
    case = _write_case(tmp_path)
    _patch_case_probe(monkeypatch)
    monkeypatch.setattr(
        compiler, "author_check", lambda *args, **kwargs: (BAD_JSON_CHECK, 0.1)
    )
    monkeypatch.setattr(
        compiler,
        "audit_checks",
        lambda **kwargs: (
            [
                {
                    "property_id": "valid-json-out",
                    "decision": "reject",
                    "reason": "manufactured output",
                }
            ],
            0.04,
            _call_summary("audit-op", 0.04),
        ),
    )
    rewrites = []

    def invalid_rewrite(**kwargs):
        rewrites.append(kwargs["prop"]["id"])
        return (
            "#!/usr/bin/env bash\nif then\n",
            0.02,
            _call_summary("rewrite-op", 0.02),
        )

    monkeypatch.setattr(
        compiler, "rewrite_semantic_check", invalid_rewrite, raising=False
    )

    with pytest.raises(RuntimeError, match="no usable property checkers"):
        compiler.compile_case(
            case, JSON_PROPERTIES[:1], "model-a", image="candidate:built"
        )

    assert rewrites == []


def test_compile_case_retries_each_mechanically_invalid_checker_before_audit(
    tmp_path, monkeypatch
):
    case = _write_case(tmp_path)
    _patch_case_probe(monkeypatch)
    authored = []

    def invalid_author(*args, **kwargs):
        authored.append(kwargs.get("fix"))
        return "echo missing-shebang\n", 0.1

    monkeypatch.setattr(compiler, "author_check", invalid_author)
    monkeypatch.setattr(
        compiler,
        "audit_checks",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("audit called")),
    )

    with pytest.raises(RuntimeError, match="no usable property checkers"):
        compiler.compile_case(
            case, JSON_PROPERTIES, "model-a", image="candidate:built"
        )

    assert len(authored) == 4


def test_checker_authoring_omits_token_limit_and_uses_time_limit(monkeypatch):
    captured = []

    def fake_chat(messages, **kwargs):
        captured.append({"messages": messages, **kwargs})
        usage = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cached_input_tokens": 0,
        }
        return {
            "content": "#!/usr/bin/env bash\nexit 0\n",
            "cost_provider_credits": 0.0,
            "operation_id": "author-op",
            "model": kwargs["model"],
            "usage": usage,
            "journal_terminal_receipt": {"usage": usage},
            "journal_terminal_receipt_sha256": "a" * 64,
            "journal_call_terminal_receipt_sha256": "b" * 64,
        }

    monkeypatch.setattr(compiler, "chat", fake_chat)
    script, cost, call = compiler.author_check(
        {"id": "p1", "nl": "the command works", "reads": "state"},
        "demo",
        "repair the command",
        ["bash", "python3"],
        ["cli.py"],
        "deepseek-v4-flash",
    )

    assert script.startswith("#!/usr/bin/env bash\n")
    assert cost == 0.0
    assert call["operation_id"] == "author-op"
    assert len(captured) == 1
    assert captured[0]["max_tokens"] is None
    assert captured[0]["timeout_seconds"] == 120
    assert "finish quickly" in captured[0]["messages"][1]["content"].lower()


def test_post_run_python_author_prompt_is_path_only_and_blinded(monkeypatch):
    captured = []

    def fake_chat(messages, **kwargs):
        captured.append({"messages": messages, **kwargs})
        usage = {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "cached_input_tokens": 0,
        }
        return {
            "content": "import sys\nprint('checked')\nsys.exit(0)\n",
            "cost_provider_credits": 0.01,
            "operation_id": "python-author-op",
            "model": kwargs["model"],
            "usage": usage,
            "journal_terminal_receipt": {"usage": usage},
            "journal_terminal_receipt_sha256": "a" * 64,
            "journal_call_terminal_receipt_sha256": "b" * 64,
        }

    monkeypatch.setattr(compiler, "chat", fake_chat)
    source, cost, call = compiler.author_python_check(
        prop={"id": "p1", "nl": "The parser accepts valid input.", "reads": "state"},
        skill="json-parser",
        task_prompt="Implement the parser.",
        environment="A repository containing input.json.",
        tools=["python3", "find"],
        final_tree=["parser.py", "input.json"],
        model="deepseek-v3.2",
    )

    prompt = captured[0]["messages"][1]["content"]
    assert source.startswith("import sys")
    assert cost == 0.01
    assert call["operation_id"] == "python-author-op"
    assert "parser.py" in prompt and "input.json" in prompt
    assert "Implement the parser." in prompt
    assert "The parser accepts valid input." in prompt
    assert "A repository containing input.json." in prompt
    for forbidden in (
        "SECRET_FILE_CONTENT",
        "workspace.diff",
        "trace.jsonl contents",
        "skillrace method",
        "previous verdict",
    ):
        assert forbidden not in prompt
    assert captured[0]["max_tokens"] is None
    assert captured[0]["timeout_seconds"] == 120
    system_prompt = captured[0]["messages"][0]["content"].lower()
    assert "never invent" in system_prompt
    assert "inspect documentation, source, or --help" in system_prompt
    assert "exit 2" in system_prompt and "underdetermined" in system_prompt


def test_post_run_compiler_retries_syntax_once_and_excludes_only_bad_property(
    tmp_path, monkeypatch
):
    authored = iter(
        [
            ("if then:\n", 0.1, _call_summary("p1-first", 0.1)),
            ("if still broken:\n", 0.2, _call_summary("p1-retry", 0.2)),
            ("import sys\nsys.exit(0)\n", 0.3, _call_summary("p2", 0.3)),
        ]
    )
    fixes = []

    def fake_author(**kwargs):
        fixes.append(kwargs.get("fix"))
        return next(authored)

    monkeypatch.setattr(compiler, "author_python_check", fake_author)
    properties = [
        {"id": "p1", "nl": "first property", "reads": "state"},
        {"id": "p2", "nl": "second property", "reads": "state"},
    ]

    manifest, cost = compiler.compile_post_run_checks(
        run_dir=tmp_path,
        properties=properties,
        candidate={
            "skill": "demo",
            "prompt": "fix it",
            "provenance": {"env_nl": "a small repository", "source": "skillrace"},
        },
        tools=["python3"],
        final_tree=["app.py"],
        snapshot_identity="sha256:final",
        model="model-a",
    )

    assert fixes[0] is None
    assert "Error" in fixes[1][1]
    assert fixes[2] is None
    assert manifest["schema"] == "post-run-python-checks/1"
    assert manifest["active_property_ids"] == ["p2"]
    assert manifest["checks"][0]["script"] == "p2.py"
    assert "semantic_audit" not in manifest
    assert manifest["excluded_properties"][0]["property_id"] == "p1"
    assert manifest["excluded_properties"][0]["reason"] == "python_syntax_invalid"
    assert cost == pytest.approx(0.6)


def test_post_run_compiler_all_excluded_is_valid_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        compiler,
        "author_python_check",
        lambda **kwargs: ("not python !!!\n", 0.1, _call_summary("bad", 0.1)),
    )

    manifest, cost = compiler.compile_post_run_checks(
        run_dir=tmp_path,
        properties=[{"id": "p1", "nl": "property", "reads": "state"}],
        candidate={"skill": "demo", "prompt": "fix", "provenance": {}},
        tools=["python3"],
        final_tree=[],
        snapshot_identity="sha256:final",
        model="model-a",
    )

    assert manifest["active_property_ids"] == []
    assert len(manifest["excluded_properties"]) == 1
    assert cost == pytest.approx(0.2)


def _accept_audit_for(property_ids):
    return (
        [
            {"property_id": property_id, "decision": "accept", "reason": "supported"}
            for property_id in property_ids
        ],
        0.0,
        _call_summary("audit-op", 0.0),
    )


def test_compile_case_retries_invalid_checker_once_then_accepts(tmp_path, monkeypatch):
    case = _write_case(tmp_path)
    _patch_case_probe(monkeypatch)
    authored = iter(
        [
            ("echo missing-shebang\n", 0.1),
            ("#!/usr/bin/env bash\ntest -d /workspace\n", 0.2),
        ]
    )
    calls = []

    def fake_author(*args, **kwargs):
        calls.append(kwargs.get("fix"))
        return next(authored)

    monkeypatch.setattr(compiler, "author_check", fake_author)
    monkeypatch.setattr(
        compiler,
        "audit_checks",
        lambda **kwargs: _accept_audit_for(["valid-json-out"]),
    )

    manifest, cost = compiler.compile_case(
        case, JSON_PROPERTIES[:1], "model-a", image="candidate:built"
    )

    assert len(calls) == 2
    assert "exact bash shebang" in calls[1][1]
    assert manifest["active_property_ids"] == ["valid-json-out"]
    assert manifest["excluded_properties"] == []
    assert cost == pytest.approx(0.3)


def test_compile_case_excludes_checker_after_one_failed_retry(tmp_path, monkeypatch):
    case = _write_case(tmp_path)
    _patch_case_probe(monkeypatch)
    authored = iter(
        [
            ("echo missing-shebang\n", 0.1),
            ("still invalid\n", 0.2),
            ("#!/usr/bin/env bash\ntest -d /workspace\n", 0.3),
        ]
    )
    monkeypatch.setattr(compiler, "author_check", lambda *a, **k: next(authored))
    audited = []

    def fake_audit(**kwargs):
        audited.append([prop["id"] for prop in kwargs["properties"]])
        return _accept_audit_for(["parses-valid"])

    monkeypatch.setattr(compiler, "audit_checks", fake_audit)

    manifest, cost = compiler.compile_case(
        case, JSON_PROPERTIES, "model-a", image="candidate:built"
    )

    assert audited == [["parses-valid"]]
    assert manifest["active_property_ids"] == ["parses-valid"]
    assert manifest["excluded_properties"][0]["property_id"] == "valid-json-out"
    assert manifest["excluded_properties"][0]["reason"] == "checker_generation_failure"
    assert cost == pytest.approx(0.6)


def test_compile_case_rejects_when_every_checker_is_excluded(tmp_path, monkeypatch):
    case = _write_case(tmp_path)
    _patch_case_probe(monkeypatch)
    calls = []

    def invalid_author(*args, **kwargs):
        calls.append(kwargs.get("fix"))
        return "not bash\n", 0.1

    monkeypatch.setattr(compiler, "author_check", invalid_author)
    monkeypatch.setattr(
        compiler,
        "audit_checks",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("audit called")),
    )

    with pytest.raises(RuntimeError, match="no usable property checkers"):
        compiler.compile_case(
            case, JSON_PROPERTIES[:1], "model-a", image="candidate:built"
        )

    assert len(calls) == 2


def test_semantic_rejection_excludes_without_rewrite(tmp_path, monkeypatch):
    case = _write_case(tmp_path)
    _patch_case_probe(monkeypatch)
    monkeypatch.setattr(
        compiler,
        "author_check",
        lambda *a, **k: ("#!/usr/bin/env bash\ntest -d /workspace\n", 0.1),
    )
    monkeypatch.setattr(
        compiler,
        "audit_checks",
        lambda **kwargs: (
            [
                {
                    "property_id": "valid-json-out",
                    "decision": "reject",
                    "reason": "unsupported requirement",
                },
                {
                    "property_id": "parses-valid",
                    "decision": "accept",
                    "reason": "supported",
                },
            ],
            0.04,
            _call_summary("audit-op", 0.04),
        ),
    )
    manifest, cost = compiler.compile_case(
        case, JSON_PROPERTIES, "model-a", image="candidate:built"
    )

    assert manifest["active_property_ids"] == ["parses-valid"]
    assert manifest["excluded_properties"][0]["reason"] == (
        "checker_semantic_rejection"
    )
    assert manifest["semantic_audit"]["rewrites"] == []
    assert cost == pytest.approx(0.24)
