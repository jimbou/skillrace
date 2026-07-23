from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from skillrace.schedules import (
    ScheduleError,
    generate_schedules,
    validate_rq1_schedule,
    validate_rq3_schedule,
)


ROOT = Path(__file__).resolve().parents[1]
SUITE = json.loads((ROOT / "experiments/manifests/rq1-skills.draft.json").read_text())


def test_generated_tracks_have_complete_separate_rq1_and_rq3_schedules(tmp_path):
    master = generate_schedules(repo_root=ROOT, out_dir=tmp_path)
    assert master["execution_policy"] == "run-one-complete-track-at-a-time"
    assert master["fixed_agent_executions"] == {
        "per_track": 4000,
        "both_tracks": 8000,
        "repair_upper_bound_both_tracks": 7200,
        "fixed_plus_repair_upper_bound": 15200,
    }
    assert master["model_calls_outside_agent_executions"] == {
        "rq3_base_generation_per_track": 10,
        "rq3_base_generation_both_tracks": 20,
        "rq3_aggregate_revision_per_track": 30,
        "rq3_aggregate_revision_both_tracks": 60,
    }
    assert [row["model"] for row in master["tracks"]] == [
        "glm-4.5-flash",
        "deepseek-v4-flash",
    ]
    for track in master["tracks"]:
        rq1 = json.loads((tmp_path / f"rq1.{track['model']}.draft.json").read_text())
        rq3 = json.loads((tmp_path / f"rq3.{track['model']}.draft.json").read_text())
        assert validate_rq1_schedule(rq1, suite=SUITE) == {
            "model": track["model"], "cells": 90, "skills": 30
        }
        assert validate_rq3_schedule(rq3) == {
            "model": track["model"], "cells": 10, "scenarios": 10
        }
        assert rq3["scenario_preparation"] == {
            "mode": "private-track-copy-exactly-once",
            "base_generation_calls_per_scenario": 1,
            "cross_track_identity": "normalized-benchmark-template-hash",
        }
        assert rq3["preparation_workers"] == 2
        assert rq1["epoch_size"] == 1
        assert rq3["scenario_workers"] == 3


def test_frozen_generation_rebinds_protocol_suite_and_every_cell(tmp_path):
    root = tmp_path / "repo"
    protocols = root / "experiments/protocols"
    manifests = root / "experiments/manifests"
    schedules = root / "experiments/schedules"
    protocols.mkdir(parents=True)
    manifests.mkdir(parents=True)

    suite = copy.deepcopy(SUITE)
    suite["status"] = "frozen"
    suite["suite_id"] = "skillrace-d1-public-v1"
    (manifests / "rq1-skills.frozen.json").write_text(json.dumps(suite))
    for model in ("glm-4.5-flash", "deepseek-v4-flash"):
        source = ROOT / f"experiments/protocols/issta-main.{model}.draft.json"
        protocol = json.loads(source.read_text())
        protocol["status"] = "frozen"
        protocol["protocol_id"] = f"skillrace-issta-main-{model}-v1"
        (protocols / f"issta-main.{model}.frozen.json").write_text(
            json.dumps(protocol)
        )

    master = generate_schedules(
        repo_root=root, out_dir=schedules, status="frozen"
    )

    assert master["status"] == "frozen"
    for model in ("glm-4.5-flash", "deepseek-v4-flash"):
        rq1 = json.loads((schedules / f"rq1.{model}.frozen.json").read_text())
        rq3 = json.loads((schedules / f"rq3.{model}.frozen.json").read_text())
        protocol_path = f"experiments/protocols/issta-main.{model}.frozen.json"
        assert rq1["status"] == rq3["status"] == "frozen"
        assert rq1["protocol"] == rq3["protocol"] == protocol_path
        assert rq1["suite_manifest"] == "experiments/manifests/rq1-skills.frozen.json"
        assert all(cell["campaign"]["protocol"] == protocol_path for cell in rq1["cells"])
        assert validate_rq1_schedule(
            rq1, suite=suite, require_frozen=True, repo_root=root
        )["cells"] == 90
        assert validate_rq3_schedule(
            rq3, require_frozen=True, repo_root=root
        )["cells"] == 10


def test_rq1_schedule_rejects_a_duplicate_even_if_cell_count_is_unchanged(tmp_path):
    generate_schedules(repo_root=ROOT, out_dir=tmp_path)
    path = tmp_path / "rq1.glm-4.5-flash.draft.json"
    schedule = json.loads(path.read_text())
    schedule["cells"][1] = copy.deepcopy(schedule["cells"][0])
    with pytest.raises(ScheduleError, match="omitted or duplicated|output paths"):
        validate_rq1_schedule(schedule, suite=SUITE)


def test_rq3_schedule_rejects_cross_model_protocol_substitution(tmp_path):
    generate_schedules(repo_root=ROOT, out_dir=tmp_path)
    path = tmp_path / "rq3.glm-4.5-flash.draft.json"
    schedule = json.loads(path.read_text())
    schedule["protocol"] = "experiments/protocols/issta-main.deepseek-v4-flash.draft.json"
    with pytest.raises(ScheduleError, match="protocol identity"):
        validate_rq3_schedule(schedule)


def test_track_runner_never_mixes_model_roots_and_uses_frozen_epoch_size():
    text = (ROOT / "scripts/run_model_track.sh").read_text(encoding="utf-8")
    assert 'ROOT="results/${MODEL}"' in text
    assert 'rq1.${MODEL}.draft.json' in text
    assert 'rq3.${MODEL}.draft.json' in text
    assert '--epoch-size 1' in text
    assert 'glm-4.5-flash|deepseek-v4-flash' in text
