from __future__ import annotations

import json
import pathlib

import pytest

from skillrace.campaign_protocol import CampaignProtocol, HEADLINE_METHODS
from skillrace.loop import resolve_campaign_protocol


def protocol_dict(**overrides):
    data = {
        "schema": "campaign-protocol/1",
        "protocol_id": "test-v1",
        "status": "draft",
        "model": "glm-4.5-flash",
        "budget": 30,
        "bootstrap_count": 10,
        "max_generation_attempts_per_execution": 5,
        "seed_generator": {
            "batch_size": 5,
            "temperature": 0.9,
            "build_retries": 4,
        },
        "greybox_level": "L1",
        "random_seed": 20260711,
        "repair": {
            "enabled": True,
            "timeout_seconds": 300,
            "max_output_tokens": 4000,
            "temperature": 0.0,
            "reasoning": True,
            "backend_by_method": {
                "random": "direct",
                "greybox": "direct",
                "skillrace": "pi",
            },
        },
    }
    data.update(overrides)
    return data


def test_lean_allocations_are_exact_and_only_three_methods_are_headline():
    protocol = CampaignProtocol.from_dict(protocol_dict())

    assert HEADLINE_METHODS == ("random", "greybox", "skillrace")
    assert protocol.allocation_for("random") == {
        "budget": 30,
        "bootstrap": 0,
        "exploration": 30,
    }
    assert protocol.allocation_for("greybox") == {
        "budget": 30,
        "bootstrap": 10,
        "exploration": 20,
    }
    assert protocol.allocation_for("skillrace") == {
        "budget": 30,
        "bootstrap": 10,
        "exploration": 20,
    }
    with pytest.raises(ValueError, match="unknown method"):
        protocol.allocation_for("seeded-blackbox")


@pytest.mark.parametrize(
    "field",
    [
        "agent_model",
        "generation_model",
        "realization_model",
        "repair_model",
        "segmentation_model",
        "tree_model",
        "guard_model",
        "check_model",
    ],
)
def test_role_specific_model_override_is_rejected(field):
    with pytest.raises(ValueError, match="same model"):
        CampaignProtocol.from_dict(protocol_dict(**{field: "different-model"}))


def test_even_redundant_role_model_fields_are_rejected_to_keep_one_control_surface():
    with pytest.raises(ValueError, match="role-specific"):
        CampaignProtocol.from_dict(
            protocol_dict(agent_model="glm-4.5-flash")
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("budget", 0),
        ("bootstrap_count", -1),
        ("bootstrap_count", 31),
        ("max_generation_attempts_per_execution", 0),
        ("greybox_level", "L0"),
        ("greybox_level", "L2"),
    ],
)
def test_invalid_budget_attempt_cap_or_nonproduction_granularity_is_rejected(field, value):
    with pytest.raises(ValueError):
        CampaignProtocol.from_dict(protocol_dict(**{field: value}))


def test_reviewable_main_protocol_is_the_exact_lean_draft():
    path = pathlib.Path("experiments/protocols/issta-main.draft.json")
    protocol = CampaignProtocol.load(path)

    assert protocol.raw == protocol_dict(
        protocol_id="skillrace-issta-main-glm-4.5-flash-v1-draft"
    )
    assert protocol.hash == CampaignProtocol.from_dict(json.loads(path.read_text())).hash


def test_unknown_or_extra_contract_fields_are_rejected():
    with pytest.raises(ValueError, match="unknown protocol field"):
        CampaignProtocol.from_dict(protocol_dict(best_level_per_skill=True))


def test_repair_policy_freezes_method_specific_patch_backends():
    protocol = CampaignProtocol.from_dict(protocol_dict())

    assert protocol.repair.timeout_seconds == 300
    assert protocol.repair.backend_for("skillrace") == "pi"
    assert protocol.repair.backend_for("greybox") == "direct"
    assert protocol.repair.backend_for("random") == "direct"


@pytest.mark.parametrize(
    "repair",
    [
        {},
        {"enabled": True},
        {
            "enabled": True,
            "timeout_seconds": 0,
            "max_output_tokens": 4000,
            "temperature": 0.0,
            "reasoning": True,
            "backend_by_method": {
                "random": "direct", "greybox": "direct", "skillrace": "pi"
            },
        },
        {
            "enabled": True,
            "timeout_seconds": 120,
            "max_output_tokens": 4000,
            "temperature": 0.0,
            "reasoning": True,
            "backend_by_method": {
                "random": "direct", "greybox": "direct", "skillrace": "agent"
            },
        },
    ],
)
def test_invalid_repair_policy_is_rejected(repair):
    with pytest.raises(ValueError, match="repair"):
        CampaignProtocol.from_dict(protocol_dict(repair=repair))


def test_only_explicit_legacy_development_resume_accepts_missing_repair(tmp_path):
    legacy = protocol_dict(
        protocol_id="development-only-old-pilot-v1",
        status="runtime",
        model="deepseek-v3.2",
        budget=2,
        bootstrap_count=1,
    )
    legacy.pop("repair")
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    with pytest.raises(ValueError, match="repair"):
        CampaignProtocol.load(path)

    resumed = CampaignProtocol.load_legacy_development_resume(path)

    assert resumed.raw == legacy
    assert resumed.repair.enabled is False
    assert resolve_campaign_protocol(
        resumed, development_only=True
    ) is resumed


@pytest.mark.parametrize(
    "change",
    [
        {"protocol_id": "old-pilot-v1"},
        {"status": "draft", "model": "glm-4.5-flash"},
    ],
)
def test_legacy_resume_never_relaxes_non_development_protocols(tmp_path, change):
    legacy = protocol_dict(**change)
    legacy.pop("repair")
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    with pytest.raises(ValueError, match="legacy development"):
        CampaignProtocol.load_legacy_development_resume(path)
