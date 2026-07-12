from __future__ import annotations

import json
import pathlib

import pytest

from skillrace.campaign_protocol import CampaignProtocol, HEADLINE_METHODS


def protocol_dict(**overrides):
    data = {
        "schema": "campaign-protocol/1",
        "protocol_id": "test-v1",
        "status": "draft",
        "model": "qwen3.6-flash",
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
            protocol_dict(agent_model="qwen3.6-flash")
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
        protocol_id="skillrace-issta-main-v1-draft"
    )
    assert protocol.hash == CampaignProtocol.from_dict(json.loads(path.read_text())).hash


def test_unknown_or_extra_contract_fields_are_rejected():
    with pytest.raises(ValueError, match="unknown protocol field"):
        CampaignProtocol.from_dict(protocol_dict(best_level_per_skill=True))
