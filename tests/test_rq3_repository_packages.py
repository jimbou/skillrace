from __future__ import annotations

import json
from pathlib import Path

from skillrace.rq3 import stage_public_scenario
from skillrace.rq3_scenario import load_public_campaign_config
from skillrace.scenario_contract import CANONICAL_SCENARIOS


ROOT = Path(__file__).parents[1] / "scenarios"


def test_all_ten_scenarios_stage_real_public_campaign_inputs_without_hidden_tests(
    tmp_path,
):
    for scenario_id in CANONICAL_SCENARIOS:
        source = ROOT / scenario_id
        config = load_public_campaign_config(source)
        assert config["scenario_id"] == scenario_id
        assert config["base_image"] == f"skillrace/rq3-{scenario_id}:base"
        assert config["containerfile_text"].startswith(
            "ARG SKILLGEN_BASE_IMAGE=skillrace/skillgen-base:0.73.1-construction\n"
            "FROM ${SKILLGEN_BASE_IMAGE}\n"
        )
        assert len(config["properties"]) >= 2
        staged = stage_public_scenario(source, tmp_path / scenario_id)
        assert (staged / "campaign" / "config.json").is_file()
        assert not (staged / "tests").exists()
        serialized = json.dumps(config)
        assert "tests/" not in serialized
        assert str((source / "tests").resolve()) not in serialized


def test_existing_unprovenanced_bases_are_truthfully_marked_for_regeneration():
    for scenario_id in CANONICAL_SCENARIOS:
        marker = ROOT / scenario_id / "base_skill" / ".skillrace" / "regeneration-required.json"
        value = json.loads(marker.read_text())
        assert value["schema"] == "skillrace-base-regeneration-required/1"
        assert value["scenario_id"] == scenario_id
        assert value["status"] == "regeneration-required"
        assert value["reason"] == "original model-call provenance was not recoverable"
