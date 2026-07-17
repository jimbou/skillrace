import json
import os
from pathlib import Path
import subprocess
from typing import Any

from skillrace_next.runtime.pi import PiRequest, run_pi


def test_run_pi_builds_bounded_yunwu_command_and_saves_sanitized_evidence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    secret = "unit-test-yunwu-secret"
    monkeypatch.setenv("yunwu_key", secret)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Read the fixture and write the requested output.", encoding="utf-8")
    output = tmp_path / "operation"
    captured: dict[str, Any] = {}

    def fake_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        output.mkdir(parents=True, exist_ok=True)
        (output / "trace.jsonl").write_text(
            json.dumps({"type": "tool_call", "tool": "write"}) + "\n",
            encoding="utf-8",
        )
        accounting = output / "accounting"
        accounting.mkdir(exist_ok=True)
        (accounting / "usage.json").write_text(
            json.dumps({"input_tokens": 12, "output_tokens": 4}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"status":"completed"}\n',
            stderr=f"provider diagnostic accidentally included {secret}",
        )

    request = PiRequest(
        operation_id="pi-unit-1",
        model="deepseek-v3.2",
        prompt_path=prompt,
        output_dir=output,
        image="skillrace-pi:test",
        allowed_tools=("read", "write"),
        max_turns=4,
        timeout_seconds=180,
    )

    result = run_pi(request, fake_runner)

    command = captured["command"]
    assert command[:3] == ["docker", "run", "--rm"]
    assert command[command.index("--provider") + 1] == "yunwu"
    assert command[command.index("--model") + 1] == "deepseek-v3.2"
    assert command[command.index("--max-turns") + 1] == "4"
    assert command[command.index("--allowed-tools") + 1] == "read,write"
    assert f"{prompt.resolve()}:/input/prompt.txt:ro" in command
    assert f"{(output / 'accounting').resolve()}:/accounting" in command
    assert captured["kwargs"]["timeout"] == 180
    assert secret not in " ".join(command)

    assert result.operation_id == "pi-unit-1"
    assert result.model == "deepseek-v3.2"
    assert result.status == "completed"
    assert result.trace_path == output / "trace.jsonl"
    assert result.usage == {"input_tokens": 12, "output_tokens": 4}
    assert result.receipt_path.is_file()
    assert secret not in result.stderr
    assert "[REDACTED]" in result.stderr
    evidence = result.receipt_path.read_text(encoding="utf-8")
    assert secret not in evidence
    assert os.environ["yunwu_key"] == secret
