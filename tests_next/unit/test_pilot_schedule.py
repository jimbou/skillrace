import json
from pathlib import Path

from skillrace_next.config import load_config
from skillrace_next.pilot import (
    PILOT_PART1_SKILLS,
    PILOT_PART2_SCENARIOS,
    prepare_pilot_schedule,
    verify_pilot_schedule,
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
        (prepared / "scenario.md").write_text(f"# {scenario_id}\n", encoding="utf-8")
        (heldout / "test-case.json").write_text("{}\n", encoding="utf-8")


def test_prepare_pilot_schedule_freezes_the_approved_eight_cells(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    output = tmp_path / "skillrace_next" / "study" / "pilot"

    manifest_path = prepare_pilot_schedule(tmp_path, output)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "skillrace-pilot-schedule/1"
    assert manifest["model_track"] == "lab/deepseek-v4-flash"
    assert manifest["iteration_budget"] == 2
    assert manifest["replicate_count"] == 1
    assert manifest["heldout_repetitions"] == 1
    assert len(manifest["cells"]) == 8
    assert [cell["input_id"] for cell in manifest["cells"][:5]] == list(
        PILOT_PART1_SKILLS
    )
    assert [cell["input_id"] for cell in manifest["cells"][5:]] == list(
        PILOT_PART2_SCENARIOS
    )

    output_roots = []
    for cell in manifest["cells"]:
        config_path = output / cell["config_path"]
        config = load_config(config_path)
        assert file_hash(config_path) == cell["config_hash"]
        assert config.provider == "lab"
        assert config.model_id == "deepseek-v4-flash"
        assert config.methods == ("random", "verigrey", "skillrace")
        assert config.iteration_budget == 2
        assert config.replicate_count == 1
        assert config.heldout_repetitions == 1
        assert config.live is True
        assert config.timeouts["pi"] == 600
        assert config.timeouts["patch"] == 600
        output_roots.append(config.output_root)
        if cell["part"] == "part2":
            assert cell["heldout_tests"] == [
                f"skillrace_next/study/part2/{cell['input_id']}/heldout/t1/test-case.json"
            ]
    assert len(set(output_roots)) == 8
    assert verify_pilot_schedule(tmp_path, manifest_path) == 8


def test_verify_pilot_schedule_rejects_changed_config(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    output = tmp_path / "skillrace_next" / "study" / "pilot"
    manifest_path = prepare_pilot_schedule(tmp_path, output)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config_path = output / manifest["cells"][0]["config_path"]
    config_path.write_text("{}\n", encoding="utf-8")

    try:
        verify_pilot_schedule(tmp_path, manifest_path)
    except ValueError as exc:
        assert "config hash mismatch" in str(exc)
    else:
        raise AssertionError("changed pilot config was accepted")


def test_prepare_pilot_schedule_uses_explicit_run_id_for_fresh_outputs(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path)
    output = tmp_path / "skillrace_next" / "study" / "pilot-v2"

    manifest_path = prepare_pilot_schedule(tmp_path, output, run_id="pilot-v2")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == "pilot-v2"
    for cell in manifest["cells"]:
        config = load_config(output / cell["config_path"])
        assert config.experiment_id.startswith("pilot-v2-")
        assert str(config.output_root).startswith(
            "out/live-contracts/pilot-v2/deepseek-v4-flash/"
        )
