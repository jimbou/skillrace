from __future__ import annotations

import hashlib
import io
import json

import pytest

import skillrace.closeai as closeai
from skillrace.closeai import nonproduction_chat_fixture
from skillrace.io_utils import canonical_json_bytes, canonical_json_hash, file_hash
from skillrace.rq3 import UncertainExternalOutcomeError
from skillrace.rq3_base import (
    FROZEN_BASE_GENERATION_CONFIG,
    build_parser,
    generate_base_skill,
    validate_base_generation,
)


def test_base_generation_cli_has_explicit_scenario_purpose_and_output():
    args = build_parser().parse_args(
        [
            "generate",
            "--scenario-id",
            "widgets",
            "--purpose",
            "scenario.md",
            "--out",
            "base_skill.generated",
        ]
    )
    assert args.command == "generate"
    assert args.scenario_id == "widgets"


def test_base_generation_records_exact_prompt_response_config_hashes_and_cost(tmp_path):
    purpose = tmp_path / "scenario.md"
    purpose.write_text("# Purpose\nTeach reliable widgets.\n")
    calls = []

    def fake_chat(messages, **settings):
        calls.append((messages, settings))
        return {
            "content": "---\nname: widgets\ndescription: Build widgets.\n---\n# Widgets\nVerify inputs.\n",
            "model": "qwen3.6-flash",
            "id": "provider-call-1",
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            "cost_usd": 0.04,
        }

    record = generate_base_skill(
        scenario_id="widgets",
        purpose_path=purpose,
        output_dir=tmp_path / "base_skill",
        chat_fn=nonproduction_chat_fixture(fake_chat),
    )

    assert len(calls) == 1
    assert calls[0][1]["model"] == "qwen3.6-flash"
    assert record["model_config"] == FROZEN_BASE_GENERATION_CONFIG
    assert record["provider_response_id_sha256"] == hashlib.sha256(
        b"provider-call-1"
    ).hexdigest()
    assert "provider_call_id" not in record
    assert record["cost_usd"] == 0.04
    assert record["skill_hash"]
    assert record["prompt_hash"]
    assert record["raw_response_hash"]
    assert record["start_hash"]
    assert (tmp_path / "base_skill" / ".skillrace" / "start.json").is_file()
    assert validate_base_generation(tmp_path / "base_skill") == record

    resumed = generate_base_skill(
        scenario_id="widgets",
        purpose_path=purpose,
        output_dir=tmp_path / "base_skill",
        chat_fn=nonproduction_chat_fixture(
            lambda *_args, **_kwargs: pytest.fail("base generation must not repeat")
        ),
    )
    assert resumed == record


def test_base_generation_refuses_unknown_model_outcome_and_tampered_skill(tmp_path):
    purpose = tmp_path / "scenario.md"
    purpose.write_text("purpose\n")

    class ProcessLost(BaseException):
        pass

    with pytest.raises(ProcessLost):
        generate_base_skill(
            scenario_id="widgets",
            purpose_path=purpose,
            output_dir=tmp_path / "base_skill",
            chat_fn=nonproduction_chat_fixture(
                lambda *_args, **_kwargs: (_ for _ in ()).throw(ProcessLost())
            ),
        )
    with pytest.raises(UncertainExternalOutcomeError, match="base-generation outcome is unknown"):
        generate_base_skill(
            scenario_id="widgets",
            purpose_path=purpose,
            output_dir=tmp_path / "base_skill",
            chat_fn=nonproduction_chat_fixture(
                lambda *_args, **_kwargs: pytest.fail("unknown call must not repeat")
            ),
        )

    # A separate completed package fails closed after content tampering.
    other = tmp_path / "other.md"
    other.write_text("other purpose\n")
    generate_base_skill(
        scenario_id="other",
        purpose_path=other,
        output_dir=tmp_path / "other_skill",
        chat_fn=nonproduction_chat_fixture(
            lambda *_args, **_kwargs: {
                "content": "# Original\n",
                "model": "qwen3.6-flash",
                "id": "fixture-other-call",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                "cost_usd": 0.0,
            }
        ),
    )
    (tmp_path / "other_skill" / "SKILL.md").write_text("# Changed\n")
    with pytest.raises(ValueError, match="skill hash"):
        validate_base_generation(tmp_path / "other_skill")


def test_base_generation_rejects_implicit_unjournaled_chat_fixture(tmp_path):
    purpose = tmp_path / "scenario.md"
    purpose.write_text("purpose\n")

    with pytest.raises(ValueError, match="nonproduction_chat_fixture"):
        generate_base_skill(
            scenario_id="widgets",
            purpose_path=purpose,
            output_dir=tmp_path / "base_skill",
            chat_fn=lambda *_args, **_kwargs: {
                "content": "# Not journalled\n",
                "model": "qwen3.6-flash",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "cost_usd": 0.1,
            },
        )


def test_base_generation_binds_stable_operation_to_redacted_terminal_receipt(tmp_path):
    purpose = tmp_path / "scenario.md"
    purpose.write_text("purpose\n")
    calls = []

    def fake_chat(_messages, **settings):
        calls.append(settings)
        return {
            "content": "# Generated\n",
            "model": "qwen3.6-flash",
            "id": "raw-provider-id-must-not-be-stored",
            "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            "cost_usd": 0.25,
        }

    record = generate_base_skill(
        scenario_id="widgets",
        purpose_path=purpose,
        output_dir=tmp_path / "base_skill",
        chat_fn=nonproduction_chat_fixture(fake_chat),
    )

    root = tmp_path / "base_skill" / ".skillrace"
    start = json.loads((root / "start.json").read_text())
    assert start["schema"] == "skillrace-base-generation-start/2"
    identity = {key: value for key, value in start.items() if key != "operation_id"}
    expected_operation = f"rq3.base.{canonical_json_hash(identity)}"
    assert start["operation_id"] == expected_operation
    assert calls[0]["operation_id"] == expected_operation
    terminal = json.loads((root / "model-call-terminal.json").read_text())
    call_terminal_path = root / "model-call-operation-terminal.json"
    call_terminal = json.loads(call_terminal_path.read_text())
    assert record["schema"] == "skillrace-base-generation/2"
    assert terminal["operation_id"] == expected_operation
    assert terminal["event"] == "terminal"
    assert terminal["status"] == "success"
    assert terminal["billing_status"] == "known"
    assert record["operation_id"] == expected_operation
    assert record["journal_terminal_event_id"] == terminal["event_id"]
    assert record["journal_terminal_receipt"] == ".skillrace/model-call-terminal.json"
    assert record["journal_terminal_receipt_hash"] == file_hash(
        root / "model-call-terminal.json"
    )
    assert record["journal_call_terminal_event_id"] == call_terminal["event_id"]
    assert record["journal_call_terminal_receipt"] == (
        ".skillrace/model-call-operation-terminal.json"
    )
    assert record["journal_call_terminal_receipt_hash"] == file_hash(
        call_terminal_path
    )
    assert call_terminal["last_retry_ordinal"] == terminal["retry_ordinal"]
    assert (root / "model-call-terminal.json").read_bytes() == (
        canonical_json_bytes(terminal) + b"\n"
    )
    assert "raw-provider-id-must-not-be-stored" not in json.dumps(record)
    assert "raw-provider-id-must-not-be-stored" not in (
        root / "model-call-terminal.json"
    ).read_text()


def test_base_validation_rejects_rehashed_wrong_provider_model(tmp_path):
    purpose = tmp_path / "scenario.md"
    purpose.write_text("purpose\n")
    output = tmp_path / "base_skill"
    generate_base_skill(
        scenario_id="widgets",
        purpose_path=purpose,
        output_dir=output,
        chat_fn=nonproduction_chat_fixture(
            lambda *_args, **_kwargs: {
                "content": "# Generated\n",
                "model": "qwen3.6-flash",
                "id": "fixture-model-check",
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                "cost_usd": 0.25,
            }
        ),
    )
    provenance = output / ".skillrace"
    record_path = provenance / "base-generation.json"
    record = json.loads(record_path.read_text())
    record["provider_model"] = "wrong-model"
    record_path.write_text(json.dumps(record))
    receipt_path = provenance / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["record_hash"] = canonical_json_hash(record)
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(ValueError, match="provider model"):
        validate_base_generation(output)


def test_base_validation_rejects_missing_journal_terminal_receipt(tmp_path):
    purpose = tmp_path / "scenario.md"
    purpose.write_text("purpose\n")
    output = tmp_path / "base_skill"
    generate_base_skill(
        scenario_id="widgets",
        purpose_path=purpose,
        output_dir=output,
        chat_fn=nonproduction_chat_fixture(
            lambda *_args, **_kwargs: {
                "content": "# Generated\n",
                "model": "qwen3.6-flash",
                "id": "fixture-missing-receipt",
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                "cost_usd": 0.25,
            }
        ),
    )
    (output / ".skillrace" / "model-call-terminal.json").unlink()

    with pytest.raises(ValueError, match="model-call terminal receipt"):
        validate_base_generation(output)


def test_base_artifact_copies_exact_durable_closeai_terminal_receipt(
    tmp_path, monkeypatch
):
    purpose = tmp_path / "scenario.md"
    purpose.write_text("purpose\n")
    ledger = tmp_path / "model-calls.jsonl"
    monkeypatch.setenv("CLOSE_API_KEY", "fixture-secret")
    monkeypatch.setenv("SKILLRACE_LEDGER", str(ledger))

    class ProviderResponse(io.BytesIO):
        status = 200
        headers = {"x-request-id": "rq3-request-1"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    provider_value = {
        "id": "rq3-response-1",
        "model": "qwen3.6-flash",
        "choices": [{"message": {"content": "# Generated\n"}}],
        "usage": {
            "prompt_tokens": 4,
            "completion_tokens": 2,
            "total_tokens": 6,
        },
    }
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda _request, timeout: ProviderResponse(json.dumps(provider_value).encode()),
    )

    record = generate_base_skill(
        scenario_id="widgets",
        purpose_path=purpose,
        output_dir=tmp_path / "base_skill",
    )

    artifact_receipt = (
        tmp_path / "base_skill" / ".skillrace" / "model-call-terminal.json"
    )
    durable_receipt = (
        ledger.with_name(f"{ledger.name}.events")
        / f"{record['journal_terminal_event_id']}.json"
    )
    artifact_call_terminal = (
        tmp_path
        / "base_skill"
        / ".skillrace"
        / "model-call-operation-terminal.json"
    )
    durable_call_terminal = (
        ledger.with_name(f"{ledger.name}.events")
        / f"{record['journal_call_terminal_event_id']}.json"
    )
    assert artifact_receipt.read_bytes() == durable_receipt.read_bytes()
    assert artifact_call_terminal.read_bytes() == durable_call_terminal.read_bytes()
    assert validate_base_generation(tmp_path / "base_skill") == record


def test_base_validation_rejects_changed_frozen_system_prompt(tmp_path):
    purpose = tmp_path / "scenario.md"
    purpose.write_text("purpose\n")
    output = tmp_path / "base_skill"
    generate_base_skill(
        scenario_id="widgets",
        purpose_path=purpose,
        output_dir=output,
        chat_fn=nonproduction_chat_fixture(
            lambda *_args, **_kwargs: {
                "content": "# Generated\n",
                "model": "qwen3.6-flash",
                "id": "fixture-base-prompt-check",
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                "cost_usd": 0.25,
            }
        ),
    )
    provenance = output / ".skillrace"
    record_path = provenance / "base-generation.json"
    record = json.loads(record_path.read_text())
    record["system_prompt"] = "Biased replacement prompt"
    record["system_prompt_hash"] = hashlib.sha256(
        record["system_prompt"].encode()
    ).hexdigest()
    record_path.write_text(json.dumps(record))
    receipt_path = provenance / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["record_hash"] = canonical_json_hash(record)
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(ValueError, match="system prompt"):
        validate_base_generation(output)


def test_base_validation_rejects_rehashed_wrong_journal_request_identity(tmp_path):
    purpose = tmp_path / "scenario.md"
    purpose.write_text("purpose\n")
    output = tmp_path / "base_skill"
    generate_base_skill(
        scenario_id="widgets",
        purpose_path=purpose,
        output_dir=output,
        chat_fn=nonproduction_chat_fixture(
            lambda *_args, **_kwargs: {
                "content": "# Generated\n",
                "model": "qwen3.6-flash",
                "id": "fixture-base-request-check",
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                "cost_usd": 0.25,
            }
        ),
    )
    provenance = output / ".skillrace"
    terminal_path = provenance / "model-call-terminal.json"
    terminal = json.loads(terminal_path.read_text())
    terminal["request_sha256"] = "0" * 64
    terminal_path.write_bytes(canonical_json_bytes(terminal) + b"\n")
    record_path = provenance / "base-generation.json"
    record = json.loads(record_path.read_text())
    record["journal_terminal_receipt_hash"] = file_hash(terminal_path)
    record_path.write_text(json.dumps(record))
    receipt_path = provenance / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["record_hash"] = canonical_json_hash(record)
    receipt_path.write_text(json.dumps(receipt))

    with pytest.raises(ValueError, match="request identity"):
        validate_base_generation(output)
