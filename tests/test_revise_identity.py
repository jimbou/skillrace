from __future__ import annotations

import copy
import hashlib
import io
import json

import pytest

import skillrace.closeai as closeai
from skillrace.closeai import nonproduction_chat_fixture
from skillrace.feedback import build_feedback_envelope
from skillrace.io_utils import canonical_json_bytes, canonical_json_hash, file_hash
from skillrace.revise_skill import (
    FROZEN_REVISION_CONFIG,
    RevisionError,
    package_hash,
    revise_skill_package,
    revision_request,
    validate_revision_artifact,
)


def _campaign(method: str, property_id: str) -> dict:
    return {
        "method": method,
        "budget": 30,
        "protocol_hash": "b" * 64,
        "complete": True,
        "attempts": [
            {
                "attempt_id": "attempt-1",
                "i": 0,
                "consume_budget": True,
                "candidate_id": "case-1",
                "runner_status": "completed",
                "oracle_status": "completed",
                "violated": [property_id],
                "regrade": {"k": 1, "reproduced": {property_id: 1}},
                "reproducible": [property_id],
                "provenance": {"task_nl": "task", "env_nl": "environment"},
            }
        ],
        "iterations": [],
        "generator_state": {},
    }


def test_revision_request_is_byte_identical_except_serialized_envelope():
    first_envelope = build_feedback_envelope(_campaign("random", "property-a"), 3600)
    second_envelope = build_feedback_envelope(_campaign("skillrace", "property-b"), 3600)

    first = revision_request("# Base skill\n", first_envelope)
    second = revision_request("# Base skill\n", second_envelope)

    assert first["system"] == second["system"]
    assert first["template"] == second["template"]
    assert first["user"].replace(first["envelope_json"], "<ENVELOPE>") == second[
        "user"
    ].replace(second["envelope_json"], "<ENVELOPE>")
    assert first["model_config"] == second["model_config"] == FROZEN_REVISION_CONFIG


def test_revision_records_full_identity_and_preserves_raw_response(tmp_path):
    skill = tmp_path / "base"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Base skill\n", encoding="utf-8")
    (skill / "properties.json").write_text('{"campaign_only": true}\n', encoding="utf-8")
    (skill / "applicability.json").write_text('{"campaign_only": true}\n', encoding="utf-8")
    envelope = build_feedback_envelope(_campaign("random", "property-a"), 3600)
    calls = []

    def fake_chat(messages, **settings):
        calls.append((copy.deepcopy(messages), settings.copy()))
        return {
            "content": "```markdown\n# Revised skill\nDo the safe thing.\n```",
            "id": "fixture-revision-call",
            "usage": {"prompt_tokens": 123, "completion_tokens": 17},
            "cost_provider_credits": 0.004,
            "model": "glm-4.5-flash",
        }

    output = tmp_path / "revision"
    record = revise_skill_package(
        skill, envelope, output, chat_fn=nonproduction_chat_fixture(fake_chat)
    )

    package = output / "skill"
    provenance = output / "provenance"
    assert (package / "SKILL.md").read_text() == "# Revised skill\nDo the safe thing.\n"
    assert (provenance / "raw-response.txt").read_text().startswith(
        "```markdown"
    )
    saved = json.loads((provenance / "revision.json").read_text())
    for field in (
        "system_prompt",
        "user_prompt",
        "model_config",
        "base_skill_hash",
        "base_package_hash",
        "envelope_hash",
        "raw_response_hash",
        "revised_skill_hash",
        "input_tokens",
        "output_tokens",
        "cost_provider_credits",
    ):
        assert field in saved
    assert saved == record
    assert calls[0][1]["model"] == "glm-4.5-flash"
    assert calls[0][1]["temperature"] == 0.0
    assert calls[0][1]["max_tokens"] == FROZEN_REVISION_CONFIG["max_tokens"]
    assert {path.relative_to(package).as_posix() for path in package.rglob("*")} == {
        "SKILL.md"
    }
    assert record["skill_package"] == "skill"
    assert record["raw_response"] == "provenance/raw-response.txt"


def test_revision_refuses_overwrite_and_unsafe_skill_symlink(tmp_path):
    skill = tmp_path / "base"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Base\n", encoding="utf-8")
    envelope = build_feedback_envelope(_campaign("random", "p"), 3600)
    output = tmp_path / "existing"
    output.mkdir()

    with pytest.raises(FileExistsError):
        revise_skill_package(skill, envelope, output, chat_fn=lambda *_a, **_k: {})

    output.rmdir()
    target = tmp_path / "outside.sh"
    target.write_text("exit 0\n", encoding="utf-8")
    (skill / "unsafe.sh").symlink_to(target)
    with pytest.raises(RevisionError, match="symlink"):
        revise_skill_package(skill, envelope, output, chat_fn=lambda *_a, **_k: {})


def test_revision_rejects_implicit_unjournaled_chat_fixture(tmp_path):
    skill = tmp_path / "base"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Base\n", encoding="utf-8")
    envelope = build_feedback_envelope(_campaign("random", "p"), 3600)

    with pytest.raises(RevisionError, match="nonproduction_chat_fixture"):
        revise_skill_package(
            skill,
            envelope,
            tmp_path / "revision",
            chat_fn=lambda *_args, **_kwargs: {
                "content": "# Revised\n",
                "model": "glm-4.5-flash",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "cost_provider_credits": 0.1,
            },
        )


def test_revision_binds_durable_start_identity_to_terminal_receipt(tmp_path):
    skill = tmp_path / "base"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Base\n", encoding="utf-8")
    envelope = build_feedback_envelope(_campaign("random", "p"), 3600)
    calls = []

    def fake_chat(_messages, **settings):
        calls.append(settings)
        return {
            "content": "# Revised\n",
            "model": "glm-4.5-flash",
            "id": "raw-revision-provider-id",
            "usage": {"prompt_tokens": 8, "completion_tokens": 3},
            "cost_provider_credits": 0.02,
        }

    output = tmp_path / "random"
    request = revision_request("# Base\n", envelope)
    durable_start_identity = {
        "schema": "skillrace-revision-start/1",
        "producer": "random",
        "base_skill_hash": file_hash(skill / "SKILL.md"),
        "base_package_hash": package_hash(skill),
        "envelope_hash": canonical_json_hash(envelope),
        "request_hash": canonical_json_hash(request),
        "model_config": FROZEN_REVISION_CONFIG,
    }
    expected_operation = f"rq3.revision.{canonical_json_hash(durable_start_identity)}"

    record = revise_skill_package(
        skill,
        envelope,
        output,
        chat_fn=nonproduction_chat_fixture(fake_chat),
    )

    terminal_path = output / "provenance" / "model-call-terminal.json"
    terminal = json.loads(terminal_path.read_text())
    call_terminal_path = output / "provenance" / "model-call-operation-terminal.json"
    call_terminal = json.loads(call_terminal_path.read_text())
    assert record["schema"] == "skillrace-revision/2"
    assert calls[0]["operation_id"] == expected_operation
    assert record["operation_id"] == expected_operation
    assert terminal["operation_id"] == expected_operation
    assert record["journal_terminal_event_id"] == terminal["event_id"]
    assert record["journal_terminal_receipt_hash"] == file_hash(terminal_path)
    assert record["journal_call_terminal_event_id"] == call_terminal["event_id"]
    assert record["journal_call_terminal_receipt"] == (
        "provenance/model-call-operation-terminal.json"
    )
    assert record["journal_call_terminal_receipt_hash"] == file_hash(
        call_terminal_path
    )
    assert call_terminal["last_retry_ordinal"] == terminal["retry_ordinal"]
    assert terminal_path.read_bytes() == canonical_json_bytes(terminal) + b"\n"
    assert "raw-revision-provider-id" not in json.dumps(record)
    assert "raw-revision-provider-id" not in terminal_path.read_text()
    assert record["operation_start_identity"] == durable_start_identity
    assert validate_revision_artifact(
        output,
        expected_base_skill_hash=file_hash(skill / "SKILL.md"),
        expected_envelope_hash=canonical_json_hash(envelope),
    ) == record


def test_revision_validation_rejects_rehashed_wrong_billing_status(tmp_path):
    skill = tmp_path / "base"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Base\n", encoding="utf-8")
    envelope = build_feedback_envelope(_campaign("random", "p"), 3600)
    output = tmp_path / "random"
    revise_skill_package(
        skill,
        envelope,
        output,
        chat_fn=nonproduction_chat_fixture(
            lambda *_args, **_kwargs: {
                "content": "# Revised\n",
                "model": "glm-4.5-flash",
                "id": "fixture-billing-check",
                "usage": {"prompt_tokens": 8, "completion_tokens": 3},
                "cost_provider_credits": 0.02,
            }
        ),
    )
    record_path = output / "provenance" / "revision.json"
    record = json.loads(record_path.read_text())
    record["billing_status"] = "unknown"
    record_path.write_text(json.dumps(record))

    with pytest.raises(RevisionError, match="billing status"):
        validate_revision_artifact(
            output,
            expected_base_skill_hash=file_hash(skill / "SKILL.md"),
            expected_envelope_hash=canonical_json_hash(envelope),
        )


def test_revision_validation_rejects_changed_frozen_system_prompt(tmp_path):
    skill = tmp_path / "base"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Base\n", encoding="utf-8")
    envelope = build_feedback_envelope(_campaign("random", "p"), 3600)
    output = tmp_path / "random"
    revise_skill_package(
        skill,
        envelope,
        output,
        chat_fn=nonproduction_chat_fixture(
            lambda *_args, **_kwargs: {
                "content": "# Revised\n",
                "model": "glm-4.5-flash",
                "id": "fixture-prompt-check",
                "usage": {"prompt_tokens": 8, "completion_tokens": 3},
                "cost_provider_credits": 0.02,
            }
        ),
    )
    record_path = output / "provenance" / "revision.json"
    record = json.loads(record_path.read_text())
    record["system_prompt"] = "Biased replacement prompt"
    record["system_prompt_hash"] = hashlib.sha256(
        record["system_prompt"].encode()
    ).hexdigest()
    record_path.write_text(json.dumps(record))

    with pytest.raises(RevisionError, match="system prompt"):
        validate_revision_artifact(
            output,
            expected_base_skill_hash=file_hash(skill / "SKILL.md"),
            expected_envelope_hash=canonical_json_hash(envelope),
        )


def test_revision_artifact_copies_exact_durable_closeai_terminal_receipt(
    tmp_path, monkeypatch
):
    skill = tmp_path / "base"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Base\n", encoding="utf-8")
    envelope = build_feedback_envelope(_campaign("random", "p"), 3600)
    ledger = tmp_path / "model-calls.jsonl"
    monkeypatch.setenv("yunwu_key", "fixture-secret")
    monkeypatch.setenv("SKILLRACE_LEDGER", str(ledger))

    class ProviderResponse(io.BytesIO):
        status = 200
        headers = {"x-request-id": "revision-request-1"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    provider_value = {
        "id": "revision-response-1",
        "model": "glm-4.5-flash",
        "choices": [{"message": {"content": "# Revised\n"}}],
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": 3,
            "total_tokens": 11,
        },
    }
    monkeypatch.setattr(
        closeai.urllib.request,
        "urlopen",
        lambda _request, timeout: ProviderResponse(json.dumps(provider_value).encode()),
    )
    output = tmp_path / "random"

    record = revise_skill_package(skill, envelope, output)

    artifact_receipt = output / "provenance" / "model-call-terminal.json"
    durable_receipt = (
        ledger.with_name(f"{ledger.name}.events")
        / f"{record['journal_terminal_event_id']}.json"
    )
    artifact_call_terminal = (
        output / "provenance" / "model-call-operation-terminal.json"
    )
    durable_call_terminal = (
        ledger.with_name(f"{ledger.name}.events")
        / f"{record['journal_call_terminal_event_id']}.json"
    )
    assert artifact_receipt.read_bytes() == durable_receipt.read_bytes()
    assert artifact_call_terminal.read_bytes() == durable_call_terminal.read_bytes()
    assert validate_revision_artifact(
        output,
        expected_base_skill_hash=file_hash(skill / "SKILL.md"),
        expected_envelope_hash=canonical_json_hash(envelope),
    ) == record
