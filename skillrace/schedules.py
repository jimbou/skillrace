"""Generate and validate complete, separate RQ1/RQ3 schedules for both model tracks."""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

from .campaign_protocol import HEADLINE_METHODS, CampaignProtocol
from .io_utils import atomic_write_json, canonical_json_hash
from .model_policy import EXPERIMENT_MODELS
from .repair_validation import RQ1_REPAIR_EVIDENCE_MAX_BYTES


SCENARIOS = (
    "argparse-cli",
    "config-parser",
    "csv-stats",
    "fix-failing-test",
    "interval-merge",
    "json-csv",
    "log-parser",
    "regex-validate",
    "sqlite-query",
    "text-template",
)
RQ1_RESOURCES = {"api": 4, "docker": 3, "agent": 3}
RQ1_CAMPAIGN_WORKERS = 6
RQ1_EPOCH_SIZE = 1
# Frozen campaigns are sequential within a cell, so three independent scenario
# processes safely supply the declared three-agent Docker/API concurrency.
RQ3_SCENARIO_WORKERS = 3
RQ3_PREPARATION_WORKERS = 2


class ScheduleError(ValueError):
    """A schedule omits, duplicates, mixes, or misconfigures an experiment cell."""


def _load(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ScheduleError(f"{path} is not a JSON object")
    return value


def _protocol_path(model: str, status: str = "draft") -> str:
    if status not in {"draft", "frozen"}:
        raise ScheduleError("schedule status must be draft or frozen")
    return f"experiments/protocols/issta-main.{model}.{status}.json"


def _suite_path(status: str = "draft") -> str:
    if status not in {"draft", "frozen"}:
        raise ScheduleError("schedule status must be draft or frozen")
    return f"experiments/manifests/rq1-skills.{status}.json"


def make_rq1_schedule(
    *,
    model: str,
    suite: dict[str, Any],
    protocol: CampaignProtocol,
    status: str = "draft",
) -> dict[str, Any]:
    if model != protocol.model or model not in EXPERIMENT_MODELS:
        raise ScheduleError("RQ1 schedule model disagrees with its protocol")
    protocol_path = _protocol_path(model, status)
    suite_path = _suite_path(status)
    if protocol.status != status or suite.get("status") != status:
        raise ScheduleError("RQ1 schedule status disagrees with protocol or suite")
    suffix = "-draft" if status == "draft" else ""
    cells = []
    for index, skill in enumerate(suite["headline_skills"]):
        skill_id = skill["id"]
        methods = HEADLINE_METHODS[index % 3 :] + HEADLINE_METHODS[: index % 3]
        for method in methods:
            cells.append(
                {
                    "id": f"rq1.{model}.{skill_id}.{method}.r01",
                    "output": f"cells/{skill_id}/{method}",
                    "campaign": {
                        "method": method,
                        "skill": skill_id,
                        "skill_dir": f"skills/{skill_id}",
                        "base": f"skillrace/{skill_id}:base-{model}",
                        "props_path": f"skills/{skill_id}/properties.json",
                        "protocol": protocol_path,
                        "wall_clock": 1800,
                    },
                }
            )
    return {
        "schema": "skillrace-experiment-manifest/1",
        "experiment_id": f"skillrace-rq1-{model}-r01{suffix}",
        "status": status,
        "model": model,
        "protocol": protocol_path,
        "protocol_hash": protocol.hash,
        "suite_manifest": suite_path,
        "suite_manifest_hash": canonical_json_hash(suite),
        "replication": 1,
        "campaign_workers": RQ1_CAMPAIGN_WORKERS,
        "epoch_size": RQ1_EPOCH_SIZE,
        "resources": dict(RQ1_RESOURCES),
        "confirmation": {"enabled": True},
        "repair": {
            "enabled": True,
            "evidence_max_bytes": RQ1_REPAIR_EVIDENCE_MAX_BYTES,
        },
        "cells": cells,
    }


def make_rq3_schedule(
    *, model: str, protocol: CampaignProtocol, status: str = "draft"
) -> dict[str, Any]:
    if model != protocol.model or model not in EXPERIMENT_MODELS:
        raise ScheduleError("RQ3 schedule model disagrees with its protocol")
    protocol_path = _protocol_path(model, status)
    if protocol.status != status:
        raise ScheduleError("RQ3 schedule status disagrees with its protocol")
    suffix = "-draft" if status == "draft" else ""
    return {
        "schema": "skillrace-rq3-schedule/1",
        "experiment_id": f"skillrace-rq3-{model}-r01{suffix}",
        "status": status,
        "model": model,
        "protocol": protocol_path,
        "protocol_hash": protocol.hash,
        "scenarios_root": "scenarios",
        "replication": 1,
        "wall_clock": 1200,
        "scenario_workers": RQ3_SCENARIO_WORKERS,
        "preparation_workers": RQ3_PREPARATION_WORKERS,
        "scenario_preparation": {
            "mode": "private-track-copy-exactly-once",
            "base_generation_calls_per_scenario": 1,
            "cross_track_identity": "normalized-benchmark-template-hash",
        },
        "cells": [
            {
                "id": f"rq3.{model}.{scenario}.r01",
                "scenario": f"scenarios/{scenario}",
                "output": f"scenarios/{scenario}",
            }
            for scenario in SCENARIOS
        ],
    }


def validate_rq1_schedule(
    value: dict[str, Any],
    *,
    suite: dict[str, Any],
    require_frozen: bool = False,
    repo_root: str | pathlib.Path = ".",
) -> dict[str, Any]:
    if value.get("schema") != "skillrace-experiment-manifest/1":
        raise ScheduleError("unsupported RQ1 schedule schema")
    model = value.get("model")
    protocol_path = pathlib.Path(str(value.get("protocol", "")))
    if not protocol_path.is_absolute():
        protocol_path = pathlib.Path(repo_root) / protocol_path
    protocol = CampaignProtocol.load(protocol_path)
    if model != protocol.model or value.get("protocol_hash") != protocol.hash:
        raise ScheduleError("RQ1 schedule protocol identity drifted")
    if value.get("suite_manifest_hash") != canonical_json_hash(suite):
        raise ScheduleError("RQ1 schedule suite identity drifted")
    if require_frozen and (
        value.get("status") != "frozen"
        or protocol.status != "frozen"
        or suite.get("status") != "frozen"
        or protocol.protocol_id != f"skillrace-issta-main-{model}-v1"
        or value.get("protocol") != _protocol_path(model, "frozen")
        or value.get("suite_manifest") != _suite_path("frozen")
    ):
        raise ScheduleError(
            "RQ1 execution requires a frozen schedule, suite, and exact track protocol"
        )
    skills = [record["id"] for record in suite["headline_skills"]]
    cells = value.get("cells")
    if not isinstance(cells, list) or len(cells) != len(skills) * len(HEADLINE_METHODS):
        raise ScheduleError("RQ1 schedule does not contain exactly 90 cells")
    coordinates = []
    outputs = []
    for cell in cells:
        campaign = cell.get("campaign", {})
        coordinate = (campaign.get("skill"), campaign.get("method"))
        coordinates.append(coordinate)
        outputs.append(cell.get("output"))
        skill, method = coordinate
        if (
            skill not in skills
            or method not in HEADLINE_METHODS
            or campaign.get("base") != f"skillrace/{skill}:base-{model}"
            or campaign.get("protocol") != value["protocol"]
            or campaign.get("props_path") != f"skills/{skill}/properties.json"
            or campaign.get("skill_dir") != f"skills/{skill}"
            or campaign.get("wall_clock") != 1800
        ):
            raise ScheduleError("RQ1 cell is not the frozen shared campaign shape")
    expected = {(skill, method) for skill in skills for method in HEADLINE_METHODS}
    if set(coordinates) != expected or len(coordinates) != len(set(coordinates)):
        raise ScheduleError("RQ1 schedule coordinates are omitted or duplicated")
    if len(outputs) != len(set(outputs)):
        raise ScheduleError("RQ1 schedule output paths are duplicated")
    if (
        value.get("resources") != RQ1_RESOURCES
        or value.get("campaign_workers") != RQ1_CAMPAIGN_WORKERS
        or value.get("epoch_size") != RQ1_EPOCH_SIZE
        or value.get("confirmation") != {"enabled": True}
        or value.get("repair")
        != {
            "enabled": True,
            "evidence_max_bytes": RQ1_REPAIR_EVIDENCE_MAX_BYTES,
        }
    ):
        raise ScheduleError("RQ1 resource/post-search policy drifted")
    return {"model": model, "cells": len(cells), "skills": len(skills)}


def validate_rq3_schedule(
    value: dict[str, Any],
    *,
    require_frozen: bool = False,
    repo_root: str | pathlib.Path = ".",
) -> dict[str, Any]:
    if value.get("schema") != "skillrace-rq3-schedule/1":
        raise ScheduleError("unsupported RQ3 schedule schema")
    model = value.get("model")
    protocol_path = pathlib.Path(str(value.get("protocol", "")))
    if not protocol_path.is_absolute():
        protocol_path = pathlib.Path(repo_root) / protocol_path
    protocol = CampaignProtocol.load(protocol_path)
    if model != protocol.model or value.get("protocol_hash") != protocol.hash:
        raise ScheduleError("RQ3 schedule protocol identity drifted")
    if require_frozen and (
        value.get("status") != "frozen"
        or protocol.status != "frozen"
        or protocol.protocol_id != f"skillrace-issta-main-{model}-v1"
        or value.get("protocol") != _protocol_path(model, "frozen")
    ):
        raise ScheduleError(
            "RQ3 execution requires a frozen schedule and exact frozen track protocol"
        )
    cells = value.get("cells")
    scenarios = [cell.get("scenario") for cell in cells] if isinstance(cells, list) else []
    outputs = [cell.get("output") for cell in cells] if isinstance(cells, list) else []
    if (
        scenarios != [f"scenarios/{name}" for name in SCENARIOS]
        or len(outputs) != len(set(outputs))
        or value.get("scenario_workers") != RQ3_SCENARIO_WORKERS
        or value.get("preparation_workers") != RQ3_PREPARATION_WORKERS
        or value.get("wall_clock") != 1200
        or value.get("replication") != 1
        or value.get("scenario_preparation")
        != {
            "mode": "private-track-copy-exactly-once",
            "base_generation_calls_per_scenario": 1,
            "cross_track_identity": "normalized-benchmark-template-hash",
        }
    ):
        raise ScheduleError("RQ3 schedule cells or resources drifted")
    return {"model": model, "cells": len(cells), "scenarios": len(SCENARIOS)}


def generate_schedules(
    *,
    repo_root: str | pathlib.Path,
    out_dir: str | pathlib.Path,
    status: str = "draft",
) -> dict[str, Any]:
    root = pathlib.Path(repo_root).resolve()
    output = pathlib.Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    suite = _load(root / _suite_path(status))
    def display(path: pathlib.Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)
    tracks = []
    for model in EXPERIMENT_MODELS:
        protocol = CampaignProtocol.load(root / _protocol_path(model, status))
        rq1 = make_rq1_schedule(
            model=model, suite=suite, protocol=protocol, status=status
        )
        rq3 = make_rq3_schedule(model=model, protocol=protocol, status=status)
        rq1_path = output / f"rq1.{model}.{status}.json"
        rq3_path = output / f"rq3.{model}.{status}.json"
        atomic_write_json(rq1_path, rq1)
        atomic_write_json(rq3_path, rq3)
        tracks.append(
            {
                "model": model,
                "rq1_schedule": display(rq1_path),
                "rq1_schedule_hash": canonical_json_hash(rq1),
                "rq1_output_root": f"results/{model}/rq1",
                "rq3_schedule": display(rq3_path),
                "rq3_schedule_hash": canonical_json_hash(rq3),
                "rq3_output_root": f"results/{model}/rq3",
            }
        )
    master = {
        "schema": "skillrace-dual-track-schedules/1",
        "status": status,
        "reporting": "separate-primary-tables-no-cross-model-pooling",
        "execution_policy": "run-one-complete-track-at-a-time",
        "fixed_agent_executions": {
            "per_track": 4000,
            "both_tracks": 8000,
            "repair_upper_bound_both_tracks": 7200,
            "fixed_plus_repair_upper_bound": 15200,
        },
        "model_calls_outside_agent_executions": {
            "rq3_base_generation_per_track": 10,
            "rq3_base_generation_both_tracks": 20,
            "rq3_aggregate_revision_per_track": 30,
            "rq3_aggregate_revision_both_tracks": 60,
        },
        "tracks": tracks,
    }
    atomic_write_json(output / f"dual-model.{status}.json", master)
    return master


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--out", default="experiments/schedules")
    parser.add_argument("--status", choices=("draft", "frozen"), default="draft")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            generate_schedules(
                repo_root=args.repo_root, out_dir=args.out, status=args.status
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
