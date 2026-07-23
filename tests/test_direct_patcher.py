from __future__ import annotations

from skillrace.closeai import nonproduction_chat_fixture
from skillrace.direct_patcher import make_direct_patcher
from skillrace.io_utils import canonical_json_hash
from skillrace.repair_validation import FailureRepairRequest
from skillrace.revise_skill import normalize_revised_skill, package_hash


def test_normalize_revised_skill_removes_accidental_outer_yaml_rule_wrapper():
    wrapped = "---\n---\nname: demo\n---\n# Fixed\n---\n"

    assert normalize_revised_skill(wrapped) == "---\nname: demo\n---\n# Fixed\n"


def test_normalize_revised_skill_removes_prompt_owned_xml_delimiter():
    echoed = "---\nname: demo\n---\n# Fixed\n</current-skill>\n"

    assert normalize_revised_skill(echoed) == "---\nname: demo\n---\n# Fixed\n"


def test_direct_patcher_is_one_blind_call_with_common_evidence_only(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    (original / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    request = FailureRepairRequest(
        method="skillrace", skill_name="demo", execution_id="e1", attempt_id="a1",
        candidate_id="c1", case_dir=tmp_path / "case", original_skill_dir=original,
        original_skill_hash=package_hash(original), failed_property_ids=("p",),
        failure_signatures=("a" * 64,), run_dir=tmp_path / "run",
        output_dir=tmp_path / "out", repair_id="repair-one",
    )
    payload = {
        "schema": "skillrace-failure-repair-evidence/1",
        "original_skill_hash": request.original_skill_hash,
        "failure_core": {"task": "exact task", "environment": "exact env"},
        "method_evidence": {"reasoning_episodes": [{"reasoning": "secret advantage"}]},
    }
    evidence = {"reviser_payload": payload, "evidence_hash": canonical_json_hash(payload)}
    calls = []

    def fake_chat(messages, **settings):
        calls.append((messages, settings))
        return {
            "content": "# Fixed\n",
            "model": "test-model",
            "id": "provider-id",
            "usage": {"prompt_tokens": 20, "completion_tokens": 4},
            "cost_provider_credits": 0.01,
        }

    patcher = make_direct_patcher(
        model="test-model", timeout_seconds=120, max_tokens=4000,
        chat_fn=nonproduction_chat_fixture(fake_chat),
    )
    result = patcher(request, evidence, tmp_path / "work")

    assert result["status"] == "completed"
    assert len(calls) == 1
    messages, settings = calls[0]
    prompt = "\n".join(message["content"] for message in messages)
    assert "exact task" in prompt and "exact env" in prompt
    assert "secret advantage" not in prompt
    assert "<current-skill>" in messages[1]["content"]
    assert "CURRENT SKILL.md:\n---" not in messages[1]["content"]
    for forbidden in ("rerun", "execute", "checker", "test", "replay", "validate"):
        assert forbidden in messages[0]["content"].lower()
    assert "cosmetic-only" in messages[0]["content"].lower()
    assert "actionable procedural guidance" in messages[0]["content"].lower()
    assert settings["timeout_seconds"] == 120
    assert not (tmp_path / "work" / "raw-response.txt").exists()
