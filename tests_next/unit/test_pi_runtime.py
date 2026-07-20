import json
import os
from pathlib import Path
import subprocess
from typing import Any

from skillrace_next.runtime import pi
from skillrace_next.runtime.pi import PiRequest, direct_provider_preflight, run_pi


def test_pi_runtime_image_name_and_metadata_are_model_independent() -> None:
    assert getattr(pi, "PI_RUNTIME_IMAGE", None) == "skillrace/pi-runtime:0.73.1"
    dockerfile = Path("skillrace_next/runtime/Dockerfile.pi-runtime").read_text(
        encoding="utf-8"
    )
    assert "deepseek" not in dockerfile.lower()
    assert "qwen" not in dockerfile.lower()
    assert 'org.skillrace.track.model="runtime-mounted"' in dockerfile
    fixture = Path("tests_next/fixtures/task/Dockerfile").read_text(encoding="utf-8")
    assert "FROM skillrace/pi-runtime:0.73.1" in fixture


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


def test_run_pi_routes_lab_alias_with_its_key_and_minimal_catalog(
    tmp_path: Path, monkeypatch: Any
) -> None:
    secret = "unit-test-lab-secret"
    monkeypatch.setenv("LAB_KEY_UNLIMITED", secret)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Write result.txt.", encoding="utf-8")
    output = tmp_path / "operation"
    captured: dict[str, Any] = {}

    def fake_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        accounting = output / "accounting"
        accounting.mkdir(parents=True, exist_ok=True)
        (accounting / "usage.json").write_text(
            json.dumps(
                {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cache_read_tokens": 100,
                    "cache_write_tokens": 0,
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", secret)

    result = run_pi(
        PiRequest(
            operation_id="pi-lab-1",
            provider="lab",
            model="deepseek-v4-flash",
            prompt_path=prompt,
            output_dir=output,
            image="skillrace-pi:test",
            allowed_tools=("write",),
            max_turns=2,
            timeout_seconds=240,
        ),
        fake_runner,
    )

    command = captured["command"]
    assert command[command.index("--provider") + 1] == "lab"
    assert command[command.index("--model") + 1] == "ds/deepseek-v4-flash"
    assert command[command.index("--key-environment") + 1] == "LAB_KEY_UNLIMITED"
    assert command[command.index("-e") + 1] == "LAB_KEY_UNLIMITED"
    assert f"{(output / 'models.json').resolve()}:/root/.pi/agent/models.json:ro" in command
    assert secret not in " ".join(command)

    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert receipt["provider"] == "lab"
    assert receipt["model"] == "deepseek-v4-flash"
    assert receipt["qualified_model"] == "lab/deepseek-v4-flash"
    assert receipt["upstream_model"] == "ds/deepseek-v4-flash"
    assert receipt["estimated_cost_usd"] == "0.0002863"
    assert secret not in result.stderr


def test_direct_preflight_routes_lab_upstream_model_and_records_actual_cost(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("LAB_KEY_UNLIMITED", "lab-secret")
    captured: dict[str, Any] = {}

    class FakeResponse:
        status = 200
        headers = {
            "x-request-id": "request-1",
            "x-litellm-response-cost": "0.00042",
        }

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "id": "response-1",
                    "model": "ds/deepseek-v4-flash",
                    "choices": [
                        {"message": {"content": "SKILLRACE_PREFLIGHT_OK"}}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                }
            ).encode()

    def fake_urlopen(request: Any, timeout: int) -> FakeResponse:
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["authorization"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = direct_provider_preflight(
        "lab", "deepseek-v4-flash", tmp_path / "preflight"
    )

    assert captured["url"] == "https://llm.xmcp.ltd/v1/chat/completions"
    assert captured["body"]["model"] == "ds/deepseek-v4-flash"
    assert captured["body"]["max_tokens"] == 128
    assert captured["authorization"] == "Bearer lab-secret"
    assert result.model == "deepseek-v4-flash"
    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert receipt["qualified_model"] == "lab/deepseek-v4-flash"
    assert receipt["upstream_model"] == "ds/deepseek-v4-flash"
    assert receipt["attempts"][0]["actual_cost_usd"] == "0.00042"
