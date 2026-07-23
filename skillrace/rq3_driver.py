"""Durable outer driver for one complete model-frozen RQ3 scenario schedule."""

from __future__ import annotations

import argparse
import json
import pathlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from .io_utils import atomic_write_json, canonical_json_hash
from .rq3_pipeline import run_rq3_scenario
from .rq3_prepare import prepare_scenario
from .schedules import validate_rq3_schedule


def run_rq3_schedule(
    schedule_path: str | pathlib.Path,
    out_dir: str | pathlib.Path,
    *,
    scenario_runner: Callable[..., dict[str, Any]] = run_rq3_scenario,
    scenario_preparer: Callable[..., dict[str, Any]] = prepare_scenario,
) -> dict[str, Any]:
    source = pathlib.Path(schedule_path)
    schedule = json.loads(source.read_text(encoding="utf-8"))
    validate_rq3_schedule(schedule, require_frozen=True)
    root = pathlib.Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    receipt_path = root / "schedule.json"
    receipt = {
        "schema": "skillrace-rq3-schedule-receipt/1",
        "schedule_hash": canonical_json_hash(schedule),
        "model": schedule["model"],
        "status": "running",
        "scenario_workers": schedule["scenario_workers"],
        "preparation_workers": schedule["preparation_workers"],
        "preparation_barrier": {
            "status": "pending",
            "receipts": [],
        },
        "cells": [
            {
                "id": cell["id"],
                "scenario": cell["scenario"],
                "output": str(root / cell["output"]),
                "status": "queued",
                "preparation_hash": None,
                "error": None,
            }
            for cell in schedule["cells"]
        ],
    }
    lock = threading.Lock()

    def save() -> None:
        atomic_write_json(receipt_path, receipt)

    def update(identifier: str, **changes: Any) -> None:
        with lock:
            row = next(item for item in receipt["cells"] if item["id"] == identifier)
            row.update(changes)
            save()

    save()

    prepared_root = root / "inputs"

    def prepare(cell: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        update(cell["id"], status="preparing")
        try:
            source = pathlib.Path(cell["scenario"]).resolve()
            prepared = prepared_root / source.name
            preparation = scenario_preparer(
                source,
                prepared,
                model=schedule["model"],
            )
            if not isinstance(preparation, dict):
                raise ValueError("RQ3 scenario preparer did not return a receipt")
            update(
                cell["id"],
                status="prepared",
                preparation_hash=canonical_json_hash(preparation),
            )
            return cell["id"], preparation
        except Exception as error:
            update(cell["id"], status="failed", error=str(error)[:500])
            return None

    preparation_results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=schedule["preparation_workers"]) as executor:
        futures = [executor.submit(prepare, cell) for cell in schedule["cells"]]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                preparation_results[result[0]] = result[1]
    if len(preparation_results) != len(schedule["cells"]):
        receipt["preparation_barrier"] = {
            "status": "failed",
            "receipts": [
                {
                    "id": cell["id"],
                    "receipt_hash": canonical_json_hash(preparation_results[cell["id"]]),
                }
                for cell in schedule["cells"]
                if cell["id"] in preparation_results
            ],
        }
        receipt["status"] = "failed"
        save()
        return json.loads(json.dumps(receipt))
    receipt["preparation_barrier"] = {
        "status": "completed",
        "receipts": [
            {
                "id": cell["id"],
                "receipt_hash": canonical_json_hash(preparation_results[cell["id"]]),
            }
            for cell in schedule["cells"]
        ],
    }
    for row in receipt["cells"]:
        row["status"] = "queued"
    save()

    def execute(cell: dict[str, Any]) -> bool:
        update(cell["id"], status="running")
        try:
            prepared = prepared_root / pathlib.Path(cell["scenario"]).name
            result = scenario_runner(
                scenario_dir=str(prepared),
                scenarios_root=str(prepared_root),
                protocol_path=schedule["protocol"],
                out_dir=root / cell["output"],
                replication=schedule["replication"],
                wall_clock=schedule["wall_clock"],
            )
            if not isinstance(result, dict) or result.get("schema") != "skillrace-rq3-manifest/2":
                raise ValueError("RQ3 scenario did not return a complete /2 manifest")
        except Exception as error:  # persist the exact failed cell; other cells may finish
            update(cell["id"], status="failed", error=str(error)[:500])
            return False
        update(cell["id"], status="completed")
        return True

    with ThreadPoolExecutor(max_workers=schedule["scenario_workers"]) as executor:
        futures = [executor.submit(execute, cell) for cell in schedule["cells"]]
        for future in as_completed(futures):
            future.result()
    receipt["status"] = (
        "completed"
        if all(cell["status"] == "completed" for cell in receipt["cells"])
        else "failed"
    )
    save()
    return json.loads(json.dumps(receipt))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    result = run_rq3_schedule(args.schedule, args.out)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
