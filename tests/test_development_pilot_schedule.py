from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dual_model_development_smoke_is_small_explicit_and_non_headline():
    path = ROOT / "experiments/schedules/development-smoke.dual-model.json"
    value = json.loads(path.read_text())
    assert value["status"] == "development-only"
    assert value["campaign_workers"] == 1
    assert value["resources"] == {"api": 1, "docker": 1, "agent": 1}
    assert value["confirmation"] == {"enabled": True}
    assert value["repair"] == {"enabled": True, "evidence_max_bytes": 3600}
    assert len(value["cells"]) == 5
    assert len({cell["id"] for cell in value["cells"]}) == 5
    assert len({cell["output"] for cell in value["cells"]}) == 5
    assert {cell["campaign"]["protocol"] for cell in value["cells"]} == {
        "experiments/protocols/pilot.glm-4.5-flash.json",
        "experiments/protocols/pilot.deepseek-v4-flash.json",
    }
    assert all(
        cell["campaign"]["development_only"] is True
        and cell["campaign"]["budget"] == 2
        and cell["campaign"]["seed_count"] == 1
        and cell["campaign"]["max_pre_agent_attempts"] == 3
        and cell["campaign"]["wall_clock"] == 300
        for cell in value["cells"]
    )
    methods = {(cell["campaign"]["protocol"], cell["campaign"]["method"]) for cell in value["cells"]}
    assert (
        "experiments/protocols/pilot.glm-4.5-flash.json",
        "greybox",
    ) in methods
    assert all(not cell["output"].startswith("results/") for cell in value["cells"])


def test_glm_only_development_smoke_keeps_the_three_method_component_check():
    path = ROOT / "experiments/schedules/development-smoke.glm.json"
    value = json.loads(path.read_text())

    assert value["status"] == "development-only"
    assert value["campaign_workers"] == 1
    assert value["resources"] == {"api": 1, "docker": 1, "agent": 1}
    assert value["confirmation"] == {"enabled": True}
    assert value["repair"] == {"enabled": True, "evidence_max_bytes": 3600}
    assert len(value["cells"]) == 3
    assert {cell["campaign"]["method"] for cell in value["cells"]} == {
        "random",
        "greybox",
        "skillrace",
    }
    assert {
        cell["campaign"]["protocol"] for cell in value["cells"]
    } == {"experiments/protocols/pilot.glm-4.5-flash.json"}
    assert all(
        cell["campaign"]["development_only"] is True
        and cell["campaign"]["budget"] == 2
        and cell["campaign"]["seed_count"] == 1
        and cell["campaign"]["max_pre_agent_attempts"] == 3
        and cell["campaign"]["wall_clock"] == 300
        and cell["output"].startswith("glm-4.5-flash/")
        for cell in value["cells"]
    )


def test_glm_v32_development_smoke_is_ten_runs_and_cannot_be_headline():
    path = ROOT / "experiments/schedules/development-smoke.glm-v32.json"
    value = json.loads(path.read_text())

    assert value["status"] == "development-only"
    assert value["campaign_workers"] == 1
    assert value["resources"] == {"api": 1, "docker": 1, "agent": 1}
    assert value["confirmation"] == {"enabled": True}
    assert value["repair"] == {"enabled": True, "evidence_max_bytes": 3600}
    assert len(value["cells"]) == 5
    assert sum(cell["campaign"]["budget"] for cell in value["cells"]) == 10
    assert len({cell["id"] for cell in value["cells"]}) == 5
    assert len({cell["output"] for cell in value["cells"]}) == 5
    assert {cell["campaign"]["method"] for cell in value["cells"]} == {
        "random",
        "greybox",
        "skillrace",
    }
    assert {cell["campaign"]["protocol"] for cell in value["cells"]} == {
        "experiments/protocols/pilot.glm-4.5-flash.json",
        "experiments/protocols/pilot.deepseek-v3.2.runtime.json",
    }
    assert all(
        cell["campaign"]["development_only"] is True
        and cell["campaign"]["budget"] == 2
        and cell["campaign"]["seed_count"] == 1
        and cell["campaign"]["max_pre_agent_attempts"] == 3
        and cell["campaign"]["wall_clock"] == 300
        and not cell["output"].startswith("results/")
        for cell in value["cells"]
    )


def test_v32_recovery_smoke_preserves_only_the_two_declared_v32_cells():
    path = ROOT / "experiments/schedules/development-smoke.v32.json"
    manifest = json.loads(path.read_text())

    assert manifest["status"] == "development-only"
    assert manifest["campaign_workers"] == 1
    assert manifest["resources"] == {"api": 1, "docker": 1, "agent": 1}
    assert manifest["confirmation"] == {"enabled": True}
    assert manifest["repair"] == {"enabled": True, "evidence_max_bytes": 3600}
    assert len(manifest["cells"]) == 2
    assert sum(cell["campaign"]["budget"] for cell in manifest["cells"]) == 4
    assert {cell["campaign"]["method"] for cell in manifest["cells"]} == {
        "random",
        "skillrace",
    }
    for cell in manifest["cells"]:
        campaign = cell["campaign"]
        assert campaign["protocol"] == (
            "experiments/protocols/pilot.deepseek-v3.2.runtime.json"
        )
        assert campaign["base"] == "skillrace/json-parser:base-deepseek-v3.2-dev"
        assert campaign["development_only"] is True


def test_v32_bounded_gate_enables_real_development_confirmation_only():
    path = ROOT / "experiments/schedules/development-gate.v32.json"
    manifest = json.loads(path.read_text())

    assert manifest["status"] == "development-only"
    assert manifest["campaign_workers"] == 1
    assert manifest["resources"] == {"api": 1, "docker": 1, "agent": 1}
    assert manifest["confirmation"] == {
        "enabled": True,
        "mode": "bounded-development",
    }
    assert manifest["repair"] == {"enabled": True, "evidence_max_bytes": 3600}
    assert len(manifest["cells"]) == 1
    campaign = manifest["cells"][0]["campaign"]
    assert campaign == {
        "method": "random",
        "skill": "json-parser",
        "skill_dir": "skills/json-parser",
        "base": "skillrace/json-parser:base-deepseek-v3.2-dev",
        "props_path": "skills/json-parser/properties.json",
        "protocol": "experiments/protocols/pilot.deepseek-v3.2.runtime.json",
        "development_only": True,
        "budget": 2,
        "seed_count": 1,
        "max_pre_agent_attempts": 3,
        "wall_clock": 300,
    }
