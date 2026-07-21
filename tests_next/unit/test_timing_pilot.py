import json
from pathlib import Path

from skillrace_next.config import load_config
from skillrace_next.pilot import (
    PILOT_PART1_SKILLS,
    PILOT_PART2_SCENARIOS,
    prepare_timing_pilot_schedule,
    verify_timing_pilot_schedule,
)
from skillrace_next.storage import file_hash


def _write_inputs(root: Path) -> None:
    for skill_id in PILOT_PART1_SKILLS:
        source = root / "skills" / skill_id
        prepared = root / "skillrace_next" / "study" / "part1" / skill_id
        source.mkdir(parents=True)
        prepared.mkdir(parents=True)
        (source / "SKILL.md").write_text(f"# {skill_id}\n", encoding="utf-8")
        (prepared / "s0-receipt.json").write_text("{}\n", encoding="utf-8")
        (prepared / "properties.json").write_text("[]\n", encoding="utf-8")
    for scenario_id in PILOT_PART2_SCENARIOS:
        prepared = root / "skillrace_next" / "study" / "part2" / scenario_id
        heldout = prepared / "heldout" / "t1"
        heldout.mkdir(parents=True)
        (prepared / "scenario.md").write_text(
            f"# {scenario_id}\n", encoding="utf-8"
        )
        (prepared / "development-properties.json").write_text(
            "[]\n", encoding="utf-8"
        )
        (heldout / "test-case.json").write_text("{}\n", encoding="utf-8")


def test_timing_pilot_freezes_both_models_over_approved_inputs(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path)
    output = tmp_path / "skillrace_next" / "study" / "timing-pilot-v1"

    manifest_path = prepare_timing_pilot_schedule(
        tmp_path, output, run_id="timing-pilot-v1"
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "skillrace-timing-pilot-schedule/1"
    assert manifest["model_tracks"] == [
        "lab/deepseek-v4-flash",
        "lab/qwen3.6-flash",
    ]
    assert manifest["temporary_weak_agent_ceiling_seconds"] == 600
    assert manifest["iteration_budget"] == 2
    assert len(manifest["cells"]) == 16
    assert verify_timing_pilot_schedule(tmp_path, manifest_path) == 16

    output_roots = []
    expected_inputs = [*PILOT_PART1_SKILLS, *PILOT_PART2_SCENARIOS]
    for model in ("deepseek-v4-flash", "qwen3.6-flash"):
        cells = [cell for cell in manifest["cells"] if cell["model_id"] == model]
        assert [cell["input_id"] for cell in cells] == expected_inputs
        for cell in cells:
            config_path = output / cell["config_path"]
            assert file_hash(config_path) == cell["config_hash"]
            config = load_config(config_path)
            assert config.provider == "lab"
            assert config.model_id == model
            assert config.methods == ("random", "verigrey", "skillrace")
            assert config.iteration_budget == 2
            assert config.replicate_count == 1
            assert config.heldout_repetitions == 1
            assert config.timeouts["pi"] == 600
            assert config.timeouts["codex"] == 600
            assert config.live is True
            output_roots.append(config.output_root)
    assert len(set(output_roots)) == 16


def test_timing_pilot_verifier_rejects_a_changed_config(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    output = tmp_path / "skillrace_next" / "study" / "timing-pilot-v1"
    manifest_path = prepare_timing_pilot_schedule(
        tmp_path, output, run_id="timing-pilot-v1"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config_path = output / manifest["cells"][0]["config_path"]
    config_path.write_text("{}\n", encoding="utf-8")

    try:
        verify_timing_pilot_schedule(tmp_path, manifest_path)
    except ValueError as error:
        assert "config hash mismatch" in str(error)
    else:
        raise AssertionError("changed timing-pilot config was accepted")
