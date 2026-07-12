"""Manifest-driven concurrent experiment scheduler with one global resource pool."""

from __future__ import annotations

import argparse
import json
import pathlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from .io_utils import atomic_write_json, canonical_json_hash
from .resource_pool import ResourcePool


def _read_manifest(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read experiment manifest {path}: {error}") from error
    if not isinstance(value, dict) or value.get("schema") != "skillrace-experiment-manifest/1":
        raise ValueError("unsupported experiment manifest schema")
    return value


def _safe_output(root: pathlib.Path, relative: Any) -> pathlib.Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("campaign output must be a nonempty relative path")
    value = pathlib.PurePosixPath(relative)
    if value.is_absolute() or ".." in value.parts or value == pathlib.PurePosixPath("."):
        raise ValueError(f"campaign output escapes experiment root: {relative!r}")
    return root.joinpath(*value.parts)


def run_experiment_manifest(
    manifest_path: str | pathlib.Path,
    out_dir: str | pathlib.Path,
    *,
    campaign_runner: Callable[..., dict[str, Any]] | None = None,
    epoch_size: int = 4,
) -> dict[str, Any]:
    manifest_path = pathlib.Path(manifest_path)
    root = pathlib.Path(out_dir)
    manifest = _read_manifest(manifest_path)
    workers = manifest.get("campaign_workers")
    resources = manifest.get("resources")
    cells = manifest.get("cells")
    if not isinstance(workers, int) or isinstance(workers, bool) or workers <= 0:
        raise ValueError("campaign_workers must be a positive integer")
    if not isinstance(resources, dict) or set(resources) != {"api", "docker", "agent"}:
        raise ValueError("manifest resources must define api, docker, and agent")
    if not isinstance(cells, list) or not cells:
        raise ValueError("experiment manifest requires at least one cell")
    if campaign_runner is None:
        from .loop import run_campaign

        campaign_runner = run_campaign

    identifiers = []
    outputs = []
    normalized = []
    for cell in cells:
        if not isinstance(cell, dict) or not isinstance(cell.get("campaign"), dict):
            raise ValueError("malformed experiment cell")
        identifier = cell.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("experiment cell id must be nonempty")
        output = _safe_output(root, cell.get("output"))
        forbidden = {"out_dir", "resource_pool", "epoch_size"}.intersection(
            cell["campaign"]
        )
        if forbidden:
            raise ValueError(f"campaign manifest may not override driver fields: {forbidden}")
        identifiers.append(identifier)
        outputs.append(str(output.resolve()))
        normalized.append((identifier, output, dict(cell["campaign"])))
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate experiment cell id")
    if len(outputs) != len(set(outputs)):
        raise ValueError("duplicate campaign output directory")

    pool = ResourcePool(**resources)
    root.mkdir(parents=True, exist_ok=True)
    schedule_path = root / "schedule.json"
    schedule = {
        "schema": "skillrace-experiment-schedule/1",
        "manifest_hash": canonical_json_hash(manifest),
        "status": "running",
        "epoch_size": epoch_size,
        "resources": dict(resources),
        "resource_peaks": pool.snapshot(),
        "cells": [
            {
                "id": identifier,
                "output": str(output),
                "status": "queued",
                "result": None,
                "error": None,
            }
            for identifier, output, _ in normalized
        ],
    }
    lock = threading.Lock()

    def save():
        schedule["resource_peaks"] = pool.snapshot()
        atomic_write_json(schedule_path, schedule)

    save()

    def update(identifier, **changes):
        with lock:
            target = next(cell for cell in schedule["cells"] if cell["id"] == identifier)
            target.update(changes)
            save()

    def run_cell(identifier, output, arguments):
        update(identifier, status="running")
        try:
            result = campaign_runner(
                **arguments,
                out_dir=output,
                resource_pool=pool,
                epoch_size=epoch_size,
            )
        except Exception as error:
            update(identifier, status="failed", error=str(error)[:500])
            return False
        terminal_complete = (
            isinstance(result, dict)
            and result.get("complete") is True
            and result.get("status") == "completed"
        )
        summary = {
            "complete": terminal_complete,
            "status": result.get("status") if isinstance(result, dict) else None,
            "campaign_path": str(output / "campaign.json"),
        }
        if not terminal_complete:
            update(
                identifier,
                status="failed",
                result=summary,
                error="campaign returned without a complete terminal result",
            )
            return False
        update(identifier, status="completed", result=summary)
        return True

    with ThreadPoolExecutor(max_workers=min(workers, len(normalized))) as executor:
        futures = {
            executor.submit(run_cell, identifier, output, arguments): identifier
            for identifier, output, arguments in normalized
        }
        for future in as_completed(futures):
            future.result()

    schedule["status"] = (
        "completed"
        if all(cell["status"] == "completed" for cell in schedule["cells"])
        else "failed"
    )
    save()
    return json.loads(json.dumps(schedule))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run a SkillRACE experiment manifest")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--epoch-size", type=int, default=4)
    args = parser.parse_args(argv)
    schedule = run_experiment_manifest(
        args.manifest, args.out, epoch_size=args.epoch_size
    )
    return 0 if schedule["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
