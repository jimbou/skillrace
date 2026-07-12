from __future__ import annotations

import json
import pathlib

import pytest

import skillrace.loop as campaign_loop
from skillrace.campaign_protocol import CampaignProtocol


def _write_skill(root):
    skill = root / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("trusted")
    (skill / "properties.json").write_text(
        json.dumps([{"id": "p1", "nl": "works", "reads": "state"}])
    )
    (skill / "applicability.json").write_text(
        json.dumps(
            {
                "skill": "demo",
                "property_ids": ["p1"],
                "fixed_invariants": [],
                "sbe_categories": [],
                "contingency": "low",
            }
        )
    )
    return skill


def test_production_cli_has_one_protocol_authority_and_no_headline_overrides():
    parser = campaign_loop.build_parser()
    args = parser.parse_args(
        [
            "--method", "random", "--skill", "demo",
            "--skill-dir", "skills/demo", "--base", "demo:base",
            "--props", "skills/demo/properties.json", "--out", "out",
        ]
    )
    assert pathlib.Path(args.protocol).name == "issta-main.draft.json"
    for forbidden in [
        "budget", "seed_count", "model", "agent_model", "greybox_level",
        "seed_k", "max_pre_agent_attempts", "random_seed",
    ]:
        assert not hasattr(args, forbidden)
    assert args.development_only is False

    pilot = parser.parse_args(
        [
            "--method", "random", "--skill", "demo",
            "--skill-dir", "skills/demo", "--base", "demo:base",
            "--props", "skills/demo/properties.json", "--out", "out",
            "--protocol", "experiments/protocols/pilot.json",
            "--development-only",
        ]
    )
    assert pilot.development_only is True


def test_production_python_path_rejects_silent_protocol_overrides_before_io(tmp_path):
    with pytest.raises(ValueError, match="development_only"):
        campaign_loop.run_campaign(
            "random", "demo", tmp_path / "missing", "demo:base", "missing.json",
            out_dir=tmp_path / "out", budget=1,
        )


def test_development_override_requires_explicit_development_only_marker(tmp_path):
    with pytest.raises(ValueError, match="development_only"):
        campaign_loop.resolve_campaign_protocol(
            None, development_only=False, budget=1, bootstrap_count=0
        )
    protocol = campaign_loop.resolve_campaign_protocol(
        None, development_only=True, budget=1, bootstrap_count=0,
        max_attempts=2,
    )
    assert isinstance(protocol, CampaignProtocol)
    assert protocol.status == "runtime"
    assert protocol.allocation_for("random")["exploration"] == 1


def _headline_data(**overrides):
    data = json.loads(
        pathlib.Path("experiments/protocols/issta-main.draft.json").read_text()
    )
    data.update(
        {
            "protocol_id": "skillrace-issta-main-v1",
            "status": "frozen",
        }
    )
    data.update(overrides)
    return data


def test_only_exact_frozen_approved_headline_protocol_can_run_non_development():
    accepted = campaign_loop.resolve_campaign_protocol(
        CampaignProtocol.from_dict(_headline_data())
    )
    assert accepted.status == "frozen"
    assert accepted.protocol_id == "skillrace-issta-main-v1"

    rejected = [
        CampaignProtocol.from_dict(_headline_data(status="draft")),
        CampaignProtocol.from_dict(_headline_data(status="runtime")),
        CampaignProtocol.from_dict(
            _headline_data(protocol_id="development-only-skillrace-issta-main-v1")
        ),
        CampaignProtocol.from_dict(_headline_data(budget=29)),
        CampaignProtocol.from_dict(_headline_data(model="different-model")),
    ]
    for protocol in rejected:
        with pytest.raises(ValueError, match="frozen headline"):
            campaign_loop.resolve_campaign_protocol(protocol)


def test_checked_in_draft_fails_closed_for_headline_execution():
    with pytest.raises(ValueError, match="frozen headline"):
        campaign_loop.resolve_campaign_protocol(
            "experiments/protocols/issta-main.draft.json"
        )


def test_props_path_is_validated_instead_of_silently_ignored(tmp_path):
    skill = _write_skill(tmp_path)
    other = tmp_path / "other-properties.json"
    other.write_text("[]")
    with pytest.raises(ValueError, match="props_path"):
        campaign_loop.run_campaign(
            "random", "demo", skill, "demo:base", other,
            out_dir=tmp_path / "out",
            protocol=CampaignProtocol.from_dict(_headline_data()),
        )


def test_run_suite_passes_only_the_reviewed_protocol_for_headline_controls():
    suite = pathlib.Path("scripts/run_suite.sh").read_text()
    assert "PROTOCOL=${PROTOCOL:-experiments/protocols/issta-main.draft.json}" in suite
    assert '--protocol "$PROTOCOL"' in suite
    for forbidden in ["--budget", "--seed-count", "--model", "--greybox-level"]:
        assert forbidden not in suite
