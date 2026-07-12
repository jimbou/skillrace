from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "closeai_hello.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("closeai_hello", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hello_uses_one_small_journaled_qwen_call():
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
        "model": "qwen3.6-flash",
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
    assert "minimal, journaled CloseAI hello call" in completed.stdout
