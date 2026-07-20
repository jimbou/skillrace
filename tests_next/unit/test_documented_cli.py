from pathlib import Path
import json

import pytest

from skillrace_next import cli
from tests_next.unit.test_config import valid_config_dict


def write_config(
    tmp_path: Path,
    part: str,
    replicate_count: int = 1,
    *,
    live: bool = False,
) -> Path:
    values = valid_config_dict()
    values["part"] = part
    values["replicate_count"] = replicate_count
    values["live"] = live
    values["output_root"] = str(tmp_path / f"{part}-run")
    path = tmp_path / f"{part}.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["live-smoke", "--help"],
        ["part1", "--help"],
        ["part2", "--help"],
        ["analyze", "--help"],
    ],
)
def test_every_documented_help_form_exits_successfully(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.main(argv)

    assert stopped.value.code == 0


@pytest.mark.parametrize("part", ["part1", "part2"])
def test_offline_part_command_loads_and_freezes_config(
    tmp_path: Path, part: str
) -> None:
    config_path = write_config(tmp_path, part)

    assert cli.main([part, "--config", str(config_path)]) == 0

    output = tmp_path / f"{part}-run"
    frozen = json.loads((output / "config.json").read_text(encoding="utf-8"))
    receipt = json.loads((output / "command.json").read_text(encoding="utf-8"))
    assert frozen["part"] == part
    assert receipt == {
        "schema": "skillrace-command/1",
        "command": part,
        "live": False,
        "status": "config_frozen",
    }


def test_part_command_rejects_config_for_other_part(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="part2 command requires"):
        cli.main(["part2", "--config", str(write_config(tmp_path, "part1"))])


def test_missing_live_flag_overrides_true_config_before_freezing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = write_config(tmp_path, "part1", live=True)

    assert cli.main(["part1", "--config", str(config_path)]) == 0

    frozen = json.loads(
        (tmp_path / "part1-run" / "config.json").read_text(encoding="utf-8")
    )
    assert frozen["live"] is False
    assert "warning: --live overrides config live=true with false" in capsys.readouterr().err


def test_live_smoke_requires_explicit_live_flag(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--live"):
        cli.main(
            [
                "live-smoke",
                "--config",
                str(write_config(tmp_path, "part1")),
                "--component",
                "pi-runtime",
            ]
        )


def test_live_smoke_runs_only_the_named_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        calls.append(command)
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = cli.main(
        [
            "live-smoke",
            "--config",
            str(write_config(tmp_path, "part1")),
            "--component",
            "pi-runtime",
            "--live",
        ]
    )

    assert result == 0
    assert len(calls) == 1
    assert "tests_next/live/test_pi_runtime_live.py" in calls[0]
    assert "--live" in calls[0]


def test_analyze_reads_summary_and_writes_analysis(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "summary.json").write_text(
        json.dumps(
            {
                "schema": "skillrace-part2/1",
                "summary": {"accepted_revisions": {"random": 1}},
            }
        ),
        encoding="utf-8",
    )

    assert cli.main(["analyze", "--run", str(run)]) == 0

    assert json.loads((run / "analysis.json").read_text(encoding="utf-8")) == {
        "schema": "skillrace-analysis/1",
        "source_schema": "skillrace-part2/1",
        "summary": {"accepted_revisions": {"random": 1}},
    }


def test_live_part1_passes_explicit_s0_and_properties_to_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = write_config(tmp_path, "part1")
    s0 = tmp_path / "s0"
    s0.mkdir()
    receipt = tmp_path / "s0-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    properties = tmp_path / "properties.json"
    properties.write_text("[]\n", encoding="utf-8")
    observed: dict[str, object] = {}

    def fake_campaign(config, s0_dir, s0_receipt, skill_id, property_path, output):
        observed.update(
            config=config,
            s0_dir=s0_dir,
            s0_receipt=s0_receipt,
            skill_id=skill_id,
            property_path=property_path,
            output=output,
        )
        return {"schema": "skillrace-part1/1"}

    monkeypatch.setattr(cli, "run_part1_campaign", fake_campaign)

    assert cli.main(
        [
            "part1",
            "--config",
            str(config_path),
            "--s0-dir",
            str(s0),
            "--s0-receipt",
            str(receipt),
            "--skill-id",
            "existing-skill",
            "--properties",
            str(properties),
            "--live",
        ]
    ) == 0

    assert observed["s0_dir"] == s0
    assert observed["s0_receipt"] == receipt
    assert observed["skill_id"] == "existing-skill"
    assert observed["property_path"] == properties
    replicate = tmp_path / "part1-run" / "replicates" / "0001"
    assert observed["config"].live is True
    assert observed["config"].output_root == replicate
    assert observed["output"] == replicate / "campaign"
    frozen = json.loads(
        (tmp_path / "part1-run" / "config.json").read_text(encoding="utf-8")
    )
    assert frozen["live"] is True
    assert "warning: --live overrides config live=false with true" in capsys.readouterr().err


def test_live_part1_runs_requested_replicates_sequentially_and_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_config(tmp_path, "part1", replicate_count=2)
    s0 = tmp_path / "s0"
    s0.mkdir()
    receipt = tmp_path / "s0-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    properties = tmp_path / "properties.json"
    properties.write_text("[]\n", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_campaign(config, s0_dir, s0_receipt, skill_id, property_path, output):
        calls.append(
            {
                "config": config,
                "s0_dir": s0_dir,
                "s0_receipt": s0_receipt,
                "skill_id": skill_id,
                "property_path": property_path,
                "output": output,
            }
        )
        return {"schema": "skillrace-part1/1"}

    monkeypatch.setattr(cli, "run_part1_campaign", fake_campaign)

    assert cli.main(
        [
            "part1",
            "--config",
            str(config_path),
            "--s0-dir",
            str(s0),
            "--s0-receipt",
            str(receipt),
            "--skill-id",
            "existing-skill",
            "--properties",
            str(properties),
            "--live",
        ]
    ) == 0

    replicate_root = tmp_path / "part1-run" / "replicates"
    assert [call["output"] for call in calls] == [
        replicate_root / "0001" / "campaign",
        replicate_root / "0002" / "campaign",
    ]
    assert [call["config"].output_root for call in calls] == [
        replicate_root / "0001",
        replicate_root / "0002",
    ]
    assert calls[0]["config"] is not calls[1]["config"]
    assert all(call["s0_dir"] == s0 for call in calls)
    assert all(call["s0_receipt"] == receipt for call in calls)
    assert all(call["skill_id"] == "existing-skill" for call in calls)
    assert all(call["property_path"] == properties for call in calls)


def test_live_part2_passes_scenario_and_hidden_tests_to_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = write_config(tmp_path, "part2")
    scenario = tmp_path / "scenario.md"
    scenario.write_text("Build a reliable transformation skill.\n", encoding="utf-8")
    hidden_a = tmp_path / "hidden-a.json"
    hidden_b = tmp_path / "hidden-b.json"
    hidden_a.write_text("{}\n", encoding="utf-8")
    hidden_b.write_text("{}\n", encoding="utf-8")
    observed: dict[str, object] = {}

    def fake_campaign(config, scenario_path, heldout_paths, output):
        observed.update(
            config=config,
            scenario_path=scenario_path,
            heldout_paths=heldout_paths,
            output=output,
        )
        return {"schema": "skillrace-part2/1"}

    monkeypatch.setattr(cli, "run_part2_campaign", fake_campaign)

    assert cli.main(
        [
            "part2",
            "--config",
            str(config_path),
            "--scenario",
            str(scenario),
            "--heldout-test",
            str(hidden_a),
            "--heldout-test",
            str(hidden_b),
            "--live",
        ]
    ) == 0

    assert observed["scenario_path"] == scenario
    assert observed["heldout_paths"] == [hidden_a, hidden_b]
    replicate = tmp_path / "part2-run" / "replicates" / "0001"
    assert observed["config"].scenario_path == scenario
    assert observed["config"].output_root == replicate
    assert observed["output"] == replicate / "campaign"
    frozen = json.loads(
        (tmp_path / "part2-run" / "config.json").read_text(encoding="utf-8")
    )
    assert frozen["scenario_path"] == str(scenario)
    assert "warning: --scenario overrides config scenario_path=" in capsys.readouterr().err


def test_failed_live_campaign_writes_terminal_command_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_config(tmp_path, "part2")
    scenario = tmp_path / "scenario.md"
    scenario.write_text("Build a reliable transformation skill.\n", encoding="utf-8")
    hidden = tmp_path / "hidden.json"
    hidden.write_text("{}\n", encoding="utf-8")

    def fail_campaign(*args, **kwargs):
        raise RuntimeError("provider stopped")

    monkeypatch.setattr(cli, "run_part2_campaign", fail_campaign)

    with pytest.raises(RuntimeError, match="provider stopped"):
        cli.main(
            [
                "part2",
                "--config",
                str(config_path),
                "--scenario",
                str(scenario),
                "--heldout-test",
                str(hidden),
                "--live",
            ]
        )

    assert json.loads(
        (tmp_path / "part2-run" / "command.json").read_text(encoding="utf-8")
    ) == {
        "schema": "skillrace-command/1",
        "command": "part2",
        "live": True,
        "status": "failed",
    }
