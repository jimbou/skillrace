"""Prepare one private, model-frozen copy of an RQ3 scenario exactly once."""

from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
from collections.abc import Callable, Mapping
from typing import Any

from .closeai import chat
from .io_utils import atomic_write_json, canonical_json_hash, file_hash
from .model_policy import require_experiment_model
from .rq3_base import generate_base_skill, validate_base_generation
from .scenario_contract import load_scenario, tree_hash


class RQ3PreparationError(ValueError):
    """A track scenario is stale, mixed-model, incomplete, or malformed."""


def _read(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RQ3PreparationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise RQ3PreparationError(f"{label} must be a JSON object")
    return value


def benchmark_template_hash(scenario_dir: str | pathlib.Path) -> str:
    """Hash the shared scenario and hidden benchmark while ignoring the base skill.

    The scenario manifest's base-skill digest is normalized away because each model
    track intentionally generates a different starting skill. Everything else,
    including all hidden tests, reference implementations, mutants, and evidence,
    remains in the cross-track identity.
    """

    root = pathlib.Path(scenario_dir).resolve()
    scenario = load_scenario(root)
    files: dict[str, Any] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RQ3PreparationError(f"scenario symlink is forbidden: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "scenario.json":
            manifest = _read(path, "scenario manifest")
            manifest.pop("base_skill_sha256", None)
            files[relative] = {"normalized": manifest}
        elif relative == "base_skill" or relative.startswith("base_skill/"):
            continue
        elif relative in {
            ".skillrace-preparation-start.json",
            ".skillrace-preparation.json",
            "base_skill.start.json",
        }:
            continue
        else:
            files[relative] = {"sha256": file_hash(path)}
    if scenario.scenario_id != root.name:
        raise RQ3PreparationError("scenario identity drifted")
    return canonical_json_hash(files)


def _validate_prepared(
    output: pathlib.Path,
    *,
    start: Mapping[str, Any],
    source: pathlib.Path,
) -> dict[str, Any]:
    saved_start = _read(output / ".skillrace-preparation-start.json", "preparation start")
    if saved_start != dict(start):
        raise RQ3PreparationError("RQ3 preparation start identity mismatch")
    record = _read(output / ".skillrace-preparation.json", "preparation receipt")
    generation = validate_base_generation(
        output / "base_skill", expected_model=str(start["model"])
    )
    scenario = load_scenario(output)
    expected = {
        "schema": "skillrace-rq3-track-scenario/1",
        "status": "prepared",
        "scenario_id": start["scenario_id"],
        "model": start["model"],
        "source_tree_hash": start["source_tree_hash"],
        "benchmark_template_hash": start["benchmark_template_hash"],
        "base_generation_id": generation["generation_id"],
        "base_generation_record_hash": canonical_json_hash(generation),
        "base_skill_hash": generation["skill_hash"],
        "base_package_hash": generation["package_hash"],
        "input_tokens": generation["input_tokens"],
        "output_tokens": generation["output_tokens"],
        "cost_provider_credits": generation["cost_provider_credits"],
    }
    if record != expected:
        raise RQ3PreparationError("RQ3 preparation receipt mismatch")
    if tree_hash(source) != start["source_tree_hash"]:
        raise RQ3PreparationError("source scenario changed after preparation")
    if benchmark_template_hash(output) != start["benchmark_template_hash"]:
        raise RQ3PreparationError("prepared hidden/public benchmark differs from source")
    if scenario.base_skill_sha256 != generation["skill_hash"]:
        raise RQ3PreparationError("prepared scenario does not bind the generated base skill")
    return record


def prepare_scenario(
    source_dir: str | pathlib.Path,
    output_dir: str | pathlib.Path,
    *,
    model: str,
    chat_fn: Callable[..., Mapping[str, Any]] = chat,
) -> dict[str, Any]:
    """Copy the fixed benchmark and generate exactly one track-specific base skill."""

    model = require_experiment_model(model)
    source = pathlib.Path(source_dir).resolve()
    source_scenario = load_scenario(source)
    output = pathlib.Path(output_dir).resolve()
    if output == source or source in output.parents or output in source.parents:
        raise RQ3PreparationError("prepared scenario must not overlap its source")
    start = {
        "schema": "skillrace-rq3-track-scenario-start/1",
        "scenario_id": source_scenario.scenario_id,
        "model": model,
        "source_tree_hash": tree_hash(source),
        "benchmark_template_hash": benchmark_template_hash(source),
    }
    start["operation_id"] = f"rq3.prepare.{canonical_json_hash(start)}"

    if not output.exists():
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = pathlib.Path(
            tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent)
        )
        try:
            copied = temporary / "scenario"
            shutil.copytree(source, copied)
            atomic_write_json(copied / ".skillrace-preparation-start.json", start)
            copied.rename(output)
            temporary.rmdir()
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    elif _read(
        output / ".skillrace-preparation-start.json", "preparation start"
    ) != start:
        raise RQ3PreparationError("existing prepared scenario has a different identity")

    receipt_path = output / ".skillrace-preparation.json"
    if receipt_path.exists():
        return _validate_prepared(output, start=start, source=source)

    base_skill = output / "base_skill"
    if base_skill.exists() and not (output / "base_skill.start.json").exists():
        shutil.rmtree(base_skill)
    generation = generate_base_skill(
        scenario_id=source_scenario.scenario_id,
        purpose_path=output / "scenario.md",
        output_dir=base_skill,
        model=model,
        chat_fn=chat_fn,
    )
    manifest_path = output / "scenario.json"
    manifest = _read(manifest_path, "scenario manifest")
    manifest["base_skill_sha256"] = generation["skill_hash"]
    atomic_write_json(manifest_path, manifest)
    load_scenario(output)
    receipt = {
        "schema": "skillrace-rq3-track-scenario/1",
        "status": "prepared",
        "scenario_id": source_scenario.scenario_id,
        "model": model,
        "source_tree_hash": start["source_tree_hash"],
        "benchmark_template_hash": start["benchmark_template_hash"],
        "base_generation_id": generation["generation_id"],
        "base_generation_record_hash": canonical_json_hash(generation),
        "base_skill_hash": generation["skill_hash"],
        "base_package_hash": generation["package_hash"],
        "input_tokens": generation["input_tokens"],
        "output_tokens": generation["output_tokens"],
        "cost_provider_credits": generation["cost_provider_credits"],
    }
    atomic_write_json(receipt_path, receipt)
    return _validate_prepared(output, start=start, source=source)
