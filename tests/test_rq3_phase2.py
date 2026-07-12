from __future__ import annotations

import copy
import hashlib
import json

import pytest

from skillrace.closeai import nonproduction_chat_fixture
from skillrace.io_utils import canonical_json_hash, file_hash
from skillrace.rq3 import (
    EVALUATION_CONDITIONS,
    ManifestMismatchError,
    UncertainExternalOutcomeError,
    campaign_record_from_file,
    project_feedback_set,
    revise_feedback_set,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _write_campaign(path, method, protocol_hash, base_hash):
    attempts = []
    for index in range(30):
        row = {
            "attempt_id": f"attempt-{index + 1:06d}",
            "i": index,
            "consume_budget": True,
            "candidate_id": f"candidate-{index + 1:03d}",
            "runner_status": "completed",
            "oracle_status": "completed",
            "violated": [],
            "inconclusive": [],
            "provenance": {"task_nl": f"task {index}", "env_nl": "public env"},
        }
        if index == 0:
            row.update(
                {
                    "violated": ["property-one"],
                    "reproducible": ["property-one"],
                    "regrade": {"k": 1, "reproduced": {"property-one": 1}},
                }
            )
        attempts.append(row)
    campaign = {
        "method": method,
        "protocol_hash": protocol_hash,
        "base_skill_hash": base_hash,
        "budget": 30,
        "allocation": {
            "budget": 30,
            "bootstrap": 0 if method == "random" else 10,
            "exploration": 30 if method == "random" else 20,
        },
        "model": "qwen3.6-flash",
        "agent_model": "qwen3.6-flash",
        "complete": True,
        "attempts": attempts,
        "iterations": attempts,
        "totals": {"runs": 30, "attempts": 30},
        "costs": {"total_usd": 1.25},
        "generator_state": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(campaign, indent=2) + "\n", encoding="utf-8")
    return campaign


def test_phase2_projects_and_resumes_three_blind_revisions(tmp_path):
    protocol_hash = _digest("protocol")
    base = tmp_path / "base"
    base.mkdir()
    (base / "SKILL.md").write_text("# Frozen base skill\n", encoding="utf-8")
    base_hash = file_hash(base / "SKILL.md")
    campaign_paths = {}
    for method in ("random", "greybox", "skillrace"):
        path = tmp_path / "campaigns" / method / "campaign.json"
        campaign = _write_campaign(path, method, protocol_hash, base_hash)
        campaign_paths[method] = path
        link = campaign_record_from_file(
            path, expected_protocol_hash=protocol_hash, expected_base_skill_hash=base_hash
        )
        assert link["artifact_hash"] == canonical_json_hash(campaign)
        assert link["counted_executions"] == 30
        assert link["cost_usd"] == 1.25

    feedback_paths, envelope_records, campaign_records = project_feedback_set(
        campaign_paths=campaign_paths,
        out_dir=tmp_path / "feedback",
        expected_protocol_hash=protocol_hash,
        expected_base_skill_hash=base_hash,
        max_bytes=4800,
    )
    assert tuple(feedback_paths) == ("random", "greybox", "skillrace")
    assert all(path.is_file() for path in feedback_paths.values())
    assert all(
        envelope_records[method]["source_campaign_hash"]
        == campaign_records[method]["artifact_hash"]
        for method in campaign_records
    )

    calls = []

    def fake_chat(messages, **settings):
        calls.append((copy.deepcopy(messages), settings.copy()))
        return {
            "content": f"# Revised skill {len(calls)}\n",
            "id": f"fixture-revision-{len(calls)}",
            "usage": {"prompt_tokens": 20, "completion_tokens": 4},
            "cost_usd": 0.02,
            "model": "qwen3.6-flash",
        }

    revision_records, skill_paths = revise_feedback_set(
        base_skill_dir=base,
        feedback_paths=feedback_paths,
        out_dir=tmp_path / "revisions",
        chat_fn=nonproduction_chat_fixture(fake_chat),
    )
    assert len(calls) == 3
    assert tuple(skill_paths) == EVALUATION_CONDITIONS
    assert skill_paths["zero-shot"] == base
    assert all(skill_paths[condition].is_dir() for condition in EVALUATION_CONDITIONS)
    assert calls[0][0][0] == calls[1][0][0] == calls[2][0][0]
    common_settings = [
        {key: value for key, value in settings.items() if key != "operation_id"}
        for _, settings in calls
    ]
    assert common_settings[0] == common_settings[1] == common_settings[2]
    assert len({settings["operation_id"] for _, settings in calls}) == 3
    assert all(record["model_config"]["model"] == "qwen3.6-flash" for record in revision_records.values())

    calls.clear()
    resumed_records, resumed_skills = revise_feedback_set(
        base_skill_dir=base,
        feedback_paths=feedback_paths,
        out_dir=tmp_path / "revisions",
        chat_fn=nonproduction_chat_fixture(
            lambda *_args, **_kwargs: pytest.fail("resume must not call the model")
        ),
    )
    assert calls == []
    assert resumed_records == revision_records
    assert resumed_skills == skill_paths


def test_phase2_refuses_stale_envelope_after_campaign_change(tmp_path):
    protocol_hash = _digest("protocol")
    base = tmp_path / "base"
    base.mkdir()
    (base / "SKILL.md").write_text("# Base\n", encoding="utf-8")
    base_hash = file_hash(base / "SKILL.md")
    paths = {}
    for method in ("random", "greybox", "skillrace"):
        path = tmp_path / "campaigns" / method / "campaign.json"
        _write_campaign(path, method, protocol_hash, base_hash)
        paths[method] = path
    project_feedback_set(
        campaign_paths=paths,
        out_dir=tmp_path / "feedback",
        expected_protocol_hash=protocol_hash,
        expected_base_skill_hash=base_hash,
        max_bytes=4800,
    )

    changed = json.loads(paths["random"].read_text())
    changed["attempts"][0]["provenance"]["task_nl"] = "changed public task"
    paths["random"].write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ManifestMismatchError, match="stale feedback"):
        project_feedback_set(
            campaign_paths=paths,
            out_dir=tmp_path / "feedback",
            expected_protocol_hash=protocol_hash,
            expected_base_skill_hash=base_hash,
            max_bytes=4800,
        )


def test_revision_start_record_blocks_duplicate_model_call_after_unknown_crash(tmp_path):
    protocol_hash = _digest("protocol")
    base = tmp_path / "base"
    base.mkdir()
    (base / "SKILL.md").write_text("# Base\n", encoding="utf-8")
    base_hash = file_hash(base / "SKILL.md")
    paths = {}
    for method in ("random", "greybox", "skillrace"):
        path = tmp_path / "campaigns" / method / "campaign.json"
        _write_campaign(path, method, protocol_hash, base_hash)
        paths[method] = path
    feedback_paths, _, _ = project_feedback_set(
        campaign_paths=paths,
        out_dir=tmp_path / "feedback",
        expected_protocol_hash=protocol_hash,
        expected_base_skill_hash=base_hash,
        max_bytes=4800,
    )

    class ProcessLost(BaseException):
        pass

    with pytest.raises(ProcessLost):
        revise_feedback_set(
            base_skill_dir=base,
            feedback_paths=feedback_paths,
            out_dir=tmp_path / "revisions",
            chat_fn=nonproduction_chat_fixture(
                lambda *_args, **_kwargs: (_ for _ in ()).throw(ProcessLost())
            ),
        )
    assert (tmp_path / "revisions" / "random.start.json").is_file()

    with pytest.raises(UncertainExternalOutcomeError, match="revision outcome is unknown"):
        revise_feedback_set(
            base_skill_dir=base,
            feedback_paths=feedback_paths,
            out_dir=tmp_path / "revisions",
            chat_fn=nonproduction_chat_fixture(
                lambda *_args, **_kwargs: pytest.fail("model call must not repeat")
            ),
        )
