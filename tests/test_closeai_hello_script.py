from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "closeai_hello.py"
YUNWU_SCRIPT = ROOT / "scripts" / "yunwu_hello.py"
YUNWU_COST_SCRIPT = ROOT / "scripts" / "yunwu_hello_cost.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("closeai_hello", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hello_uses_one_small_journaled_glm_call():
    calls = []

    def fake_chat(messages, **settings):
        calls.append((messages, settings))
        return {"content": "Hello from CloseAI!"}

    module = _load_script()
    reply = module.hello(fake_chat, operation_id="manual.hello.test")

    assert reply == "Hello from CloseAI!"
    assert len(calls) == 1
    messages, settings = calls[0]
    assert messages == [{"role": "user", "content": "Say hello in one short sentence."}]
    assert settings == {
        "model": "glm-4.5-flash",
        "temperature": 0.0,
        "max_tokens": 32,
        "retries": 1,
        "reasoning": False,
        "tag": "manual.hello",
        "operation_id": "manual.hello.test",
        "timeout_seconds": 30,
    }


def test_help_runs_directly_from_the_repository_without_installing_the_package():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "minimal, journaled Yunwu/GLM compatibility hello call" in completed.stdout


def test_canonical_yunwu_hello_defaults_to_selected_glm_track():
    spec = importlib.util.spec_from_file_location("yunwu_hello", YUNWU_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def fake_chat(messages, **settings):
        calls.append((messages, settings))
        return {"content": "ok"}

    assert module.hello(fake_chat, operation_id="manual.yunwu.test") == "ok"
    assert calls[0][1]["model"] == "glm-4.5-flash"


def test_cost_yunwu_hello_returns_compact_result():
    spec = importlib.util.spec_from_file_location("yunwu_hello_cost", YUNWU_COST_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls = []

    def fake_chat(_messages, **settings):
        calls.append(settings)
        return {
            "content": "ok",
            "usage": {
                "prompt_tokens": 8,
                "completion_tokens": 3,
                "total_tokens": 11,
            },
            "cost_provider_credits": 0.02,
            "model": "ignored-extra-field",
        }

    assert module.hello(fake_chat, operation_id="manual.yunwu-cost.test") == {
        "model": "glm-4.5-flash",
        "content": "ok",
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": 3,
            "total_tokens": 11,
        },
        "cost_provider_credits": 0.02,
    }
    assert calls == [
        {
            "model": "glm-4.5-flash",
            "temperature": 0.0,
            "max_tokens": 32,
            "retries": 1,
            "reasoning": False,
            "tag": "manual.yunwu-hello-cost",
            "operation_id": "manual.yunwu-cost.test",
            "timeout_seconds": 180,
            "journal_mode": "development",
        }
    ]


def test_canonical_yunwu_hello_prints_compact_result_as_json(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("yunwu_hello_cost", YUNWU_COST_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = {
        "content": "ok",
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": 3,
            "total_tokens": 11,
        },
        "cost_provider_credits": 0.02,
    }
    monkeypatch.setattr(module, "hello", lambda **_settings: result)
    monkeypatch.setattr(sys, "argv", [str(YUNWU_COST_SCRIPT)])

    assert module.main() == 0
    assert json.loads(capsys.readouterr().out) == result
