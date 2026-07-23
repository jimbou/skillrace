from __future__ import annotations

import json
from pathlib import Path

from skillrace.rq3_driver import run_rq3_schedule
from skillrace.io_utils import canonical_json_hash
from skillrace.schedules import generate_schedules


def _frozen_schedule(generated: Path, model: str, execution_root: Path) -> Path:
    schedule_path = generated / f"rq3.{model}.draft.json"
    schedule = json.loads(schedule_path.read_text())
    source_protocol = json.loads(Path(schedule["protocol"]).read_text())
    source_protocol["status"] = "frozen"
    source_protocol["protocol_id"] = f"skillrace-issta-main-{model}-v1"
    protocol_path = (
        execution_root
        / "experiments"
        / "protocols"
        / f"issta-main.{model}.frozen.json"
    )
    protocol_path.parent.mkdir(parents=True, exist_ok=True)
    protocol_path.write_text(json.dumps(source_protocol))
    schedule["status"] = "frozen"
    schedule["experiment_id"] = f"skillrace-rq3-{model}-r01"
    schedule["protocol"] = f"experiments/protocols/issta-main.{model}.frozen.json"
    schedule["protocol_hash"] = canonical_json_hash(source_protocol)
    frozen = generated / f"rq3.{model}.frozen.json"
    frozen.write_text(json.dumps(schedule))
    return frozen


def test_rq3_driver_runs_every_scenario_once_and_persists_terminal_receipt(
    tmp_path, monkeypatch
):
    generated = tmp_path / "generated"
    generate_schedules(repo_root=".", out_dir=generated)
    schedule = _frozen_schedule(generated, "glm-4.5-flash", tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = []
    preparations = []
    events = []

    def prepare(source, output, *, model):
        events.append(f"prepare:{source.name}")
        preparations.append((source, output, model))
        output.mkdir(parents=True, exist_ok=True)
        return {"prepared_scenario": str(output), "model": model}

    def runner(**settings):
        events.append(f"run:{Path(settings['scenario_dir']).name}")
        calls.append(settings)
        return {"schema": "skillrace-rq3-manifest/2", "rq3_id": "fixture"}

    output = tmp_path / "results"
    receipt = run_rq3_schedule(
        schedule,
        output,
        scenario_runner=runner,
        scenario_preparer=prepare,
    )
    assert receipt["status"] == "completed"
    assert len(calls) == 10
    assert len(preparations) == 10
    assert all(event.startswith("prepare:") for event in events[:10])
    assert all(event.startswith("run:") for event in events[10:])
    assert receipt["preparation_barrier"]["status"] == "completed"
    assert len(receipt["preparation_barrier"]["receipts"]) == 10
    assert all(model == "glm-4.5-flash" for _, _, model in preparations)
    assert all("/inputs/" in call["scenario_dir"] for call in calls)
    assert all(
        call["protocol_path"].endswith(
            "experiments/protocols/issta-main.glm-4.5-flash.frozen.json"
        )
        for call in calls
    )
    assert all(call["wall_clock"] == 1200 for call in calls)
    assert json.loads((output / "schedule.json").read_text()) == receipt


def test_rq3_driver_records_a_failed_scenario_without_calling_it_success(
    tmp_path, monkeypatch
):
    generated = tmp_path / "generated"
    generate_schedules(repo_root=".", out_dir=generated)

    def runner(**settings):
        if settings["scenario_dir"].endswith("csv-stats"):
            raise RuntimeError("fixture failure")
        return {"schema": "skillrace-rq3-manifest/2"}

    schedule = _frozen_schedule(generated, "deepseek-v4-flash", tmp_path)
    monkeypatch.chdir(tmp_path)
    receipt = run_rq3_schedule(
        schedule,
        tmp_path / "results",
        scenario_runner=runner,
        scenario_preparer=lambda _source, output, *, model: (
            output.mkdir(parents=True, exist_ok=True)
            or {"prepared_scenario": str(output), "model": model}
        ),
    )
    assert receipt["status"] == "failed"
    failed = [row for row in receipt["cells"] if row["status"] == "failed"]
    assert len(failed) == 1
    assert "fixture failure" in failed[0]["error"]


def test_rq3_driver_rejects_draft_before_preparation_or_scenario_execution(tmp_path):
    generated = tmp_path / "generated"
    generate_schedules(repo_root=".", out_dir=generated)
    events = []

    try:
        run_rq3_schedule(
            generated / "rq3.glm-4.5-flash.draft.json",
            tmp_path / "results",
            scenario_preparer=lambda *_args, **_kwargs: events.append("prepare"),
            scenario_runner=lambda **_kwargs: events.append("run"),
        )
    except ValueError as error:
        assert "frozen" in str(error)
    else:
        raise AssertionError("draft RQ3 schedule should fail closed")
    assert events == []
    assert not (tmp_path / "results").exists()


def test_one_preparation_failure_blocks_every_scenario_campaign(tmp_path, monkeypatch):
    generated = tmp_path / "generated"
    generate_schedules(repo_root=".", out_dir=generated)
    scenario_calls = []

    def prepare(source, output, *, model):
        if source.name == "csv-stats":
            raise RuntimeError("preparation failed")
        output.mkdir(parents=True, exist_ok=True)
        return {"scenario": source.name, "model": model}

    schedule = _frozen_schedule(generated, "glm-4.5-flash", tmp_path)
    monkeypatch.chdir(tmp_path)
    receipt = run_rq3_schedule(
        schedule,
        tmp_path / "results",
        scenario_preparer=prepare,
        scenario_runner=lambda **kwargs: scenario_calls.append(kwargs) or {},
    )

    assert receipt["status"] == "failed"
    assert receipt["preparation_barrier"]["status"] == "failed"
    assert scenario_calls == []
