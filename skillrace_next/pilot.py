import json
from pathlib import Path
from typing import Any

from .config import load_config
from .records import ExperimentConfig
from .storage import atomic_write_json, file_hash, tree_hash


PILOT_PART1_SKILLS = (
    "file-check",
    "js-feature",
    "csv-workbench",
    "fix-failing-test",
    "regex-expert",
)

PILOT_PART2_SCENARIOS = (
    "text-template",
    "csv-stats",
    "fix-failing-test",
)


def _config(
    *,
    experiment_id: str,
    model_id: str,
    part: str,
    suite_path: Path,
    scenario_path: Path,
    output_root: Path,
) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id=experiment_id,
        part=part,
        methods=("random", "verigrey", "skillrace"),
        replicate_count=1,
        provider="lab",
        model_id=model_id,
        pi_version="0.73.1",
        role_budgets={
            "proposer": 4,
            "weak_agent": 4,
            "patcher": 10,
            "segmenter": 4,
            "tree_alignment": 4,
            "skill_generator": 6,
        },
        verifier_backend="codex",
        verifier_command=("codex", "exec"),
        verifier_model="gpt-5.6-terra",
        verifier_reasoning="medium",
        docker_image="skillrace-next/task-fixture:test",
        resource_limits={"cpus": "1", "memory_mb": 512},
        network_policy="host",
        timeouts={
            "provider": 60,
            "pi": 600,
            "docker": 180,
            "codex": 600,
            "check": 60,
            "patch": 600,
        },
        suite_path=suite_path,
        scenario_path=scenario_path,
        iteration_budget=2,
        live=True,
        output_root=output_root,
        heldout_repetitions=1,
    )


def prepare_pilot_schedule(
    repo_root: str | Path,
    output_root: str | Path,
    *,
    run_id: str = "pilot",
) -> Path:
    repo = Path(repo_root)
    output = Path(output_root)
    if output.exists():
        raise FileExistsError(f"pilot schedule already exists: {output}")

    cells: list[dict[str, Any]] = []
    for skill_id in PILOT_PART1_SKILLS:
        s0 = Path("skills") / skill_id
        prepared = Path("skillrace_next/study/part1") / skill_id
        receipt = prepared / "s0-receipt.json"
        properties = prepared / "properties.json"
        for path in (repo / s0 / "SKILL.md", repo / receipt, repo / properties):
            if not path.is_file():
                raise ValueError(f"pilot Part I input is missing: {path}")
        config_path = Path("part1") / skill_id / "config.json"
        config = _config(
            experiment_id=f"{run_id}-part1-{skill_id}-deepseek-v4-flash",
            model_id="deepseek-v4-flash",
            part="part1",
            suite_path=Path("skillrace_next/study/part1"),
            scenario_path=properties,
            output_root=(
                Path("out/live-contracts")
                / run_id
                / "deepseek-v4-flash/part1"
                / skill_id
            ),
        )
        atomic_write_json(output / config_path, config.to_dict())
        cells.append(
            {
                "cell_id": f"part1/{skill_id}",
                "part": "part1",
                "input_id": skill_id,
                "config_path": config_path.as_posix(),
                "config_hash": file_hash(output / config_path),
                "s0_directory": s0.as_posix(),
                "s0_tree_hash": tree_hash(repo / s0),
                "s0_receipt": receipt.as_posix(),
                "s0_receipt_hash": file_hash(repo / receipt),
                "properties": properties.as_posix(),
                "properties_hash": file_hash(repo / properties),
            }
        )

    for scenario_id in PILOT_PART2_SCENARIOS:
        prepared = Path("skillrace_next/study/part2") / scenario_id
        scenario = prepared / "scenario.md"
        heldout = prepared / "heldout/t1/test-case.json"
        for path in (repo / scenario, repo / heldout):
            if not path.is_file():
                raise ValueError(f"pilot Part II input is missing: {path}")
        config_path = Path("part2") / scenario_id / "config.json"
        config = _config(
            experiment_id=f"{run_id}-part2-{scenario_id}-deepseek-v4-flash",
            model_id="deepseek-v4-flash",
            part="part2",
            suite_path=prepared,
            scenario_path=scenario,
            output_root=(
                Path("out/live-contracts")
                / run_id
                / "deepseek-v4-flash/part2"
                / scenario_id
            ),
        )
        atomic_write_json(output / config_path, config.to_dict())
        cells.append(
            {
                "cell_id": f"part2/{scenario_id}",
                "part": "part2",
                "input_id": scenario_id,
                "config_path": config_path.as_posix(),
                "config_hash": file_hash(output / config_path),
                "scenario": scenario.as_posix(),
                "scenario_hash": file_hash(repo / scenario),
                "heldout_tests": [heldout.as_posix()],
                "heldout_test_hashes": [file_hash(repo / heldout)],
            }
        )

    manifest_path = output / "schedule.json"
    atomic_write_json(
        manifest_path,
        {
            "schema": "skillrace-pilot-schedule/1",
            "run_id": run_id,
            "model_track": "lab/deepseek-v4-flash",
            "iteration_budget": 2,
            "replicate_count": 1,
            "heldout_repetitions": 1,
            "part2_heldout_policy": "pilot uses t1 only; t2-t10 remain reserved",
            "cells": cells,
        },
    )
    return manifest_path


TIMING_PILOT_MODELS = ("deepseek-v4-flash", "qwen3.6-flash")


def prepare_timing_pilot_schedule(
    repo_root: str | Path,
    output_root: str | Path,
    *,
    run_id: str = "timing-pilot-v1",
) -> Path:
    repo = Path(repo_root)
    output = Path(output_root)
    if output.exists():
        raise FileExistsError(f"timing pilot schedule already exists: {output}")
    cells: list[dict[str, Any]] = []
    for model_id in TIMING_PILOT_MODELS:
        for skill_id in PILOT_PART1_SKILLS:
            s0 = Path("skills") / skill_id
            prepared = Path("skillrace_next/study/part1") / skill_id
            receipt = prepared / "s0-receipt.json"
            properties = prepared / "properties.json"
            for path in (repo / s0 / "SKILL.md", repo / receipt, repo / properties):
                if not path.is_file():
                    raise ValueError(f"timing pilot Part I input is missing: {path}")
            config_path = Path(model_id) / "part1" / skill_id / "config.json"
            config = _config(
                experiment_id=f"{run_id}-part1-{skill_id}-{model_id}",
                model_id=model_id,
                part="part1",
                suite_path=Path("skillrace_next/study/part1"),
                scenario_path=properties,
                output_root=(
                    Path("out/live-contracts")
                    / run_id
                    / model_id
                    / "part1"
                    / skill_id
                ),
            )
            atomic_write_json(output / config_path, config.to_dict())
            cells.append(
                {
                    "cell_id": f"{model_id}/part1/{skill_id}",
                    "model_id": model_id,
                    "part": "part1",
                    "input_id": skill_id,
                    "config_path": config_path.as_posix(),
                    "config_hash": file_hash(output / config_path),
                    "s0_directory": s0.as_posix(),
                    "s0_tree_hash": tree_hash(repo / s0),
                    "s0_receipt": receipt.as_posix(),
                    "s0_receipt_hash": file_hash(repo / receipt),
                    "properties": properties.as_posix(),
                    "properties_hash": file_hash(repo / properties),
                }
            )
        for scenario_id in PILOT_PART2_SCENARIOS:
            prepared = Path("skillrace_next/study/part2") / scenario_id
            scenario = prepared / "scenario.md"
            properties = prepared / "development-properties.json"
            heldout = prepared / "heldout/t1/test-case.json"
            for path in (repo / scenario, repo / properties, repo / heldout):
                if not path.is_file():
                    raise ValueError(f"timing pilot Part II input is missing: {path}")
            config_path = Path(model_id) / "part2" / scenario_id / "config.json"
            config = _config(
                experiment_id=f"{run_id}-part2-{scenario_id}-{model_id}",
                model_id=model_id,
                part="part2",
                suite_path=prepared,
                scenario_path=scenario,
                output_root=(
                    Path("out/live-contracts")
                    / run_id
                    / model_id
                    / "part2"
                    / scenario_id
                ),
            )
            atomic_write_json(output / config_path, config.to_dict())
            cells.append(
                {
                    "cell_id": f"{model_id}/part2/{scenario_id}",
                    "model_id": model_id,
                    "part": "part2",
                    "input_id": scenario_id,
                    "config_path": config_path.as_posix(),
                    "config_hash": file_hash(output / config_path),
                    "scenario": scenario.as_posix(),
                    "scenario_hash": file_hash(repo / scenario),
                    "properties": properties.as_posix(),
                    "properties_hash": file_hash(repo / properties),
                    "heldout_tests": [heldout.as_posix()],
                    "heldout_test_hashes": [file_hash(repo / heldout)],
                }
            )
    manifest_path = output / "schedule.json"
    atomic_write_json(
        manifest_path,
        {
            "schema": "skillrace-timing-pilot-schedule/1",
            "run_id": run_id,
            "model_tracks": [f"lab/{model}" for model in TIMING_PILOT_MODELS],
            "temporary_weak_agent_ceiling_seconds": 600,
            "iteration_budget": 2,
            "replicate_count": 1,
            "heldout_repetitions": 1,
            "part2_heldout_policy": "timing pilot uses t1 only",
            "cells": cells,
        },
    )
    return manifest_path


def verify_timing_pilot_schedule(
    repo_root: str | Path, manifest_path: str | Path
) -> int:
    repo = Path(repo_root)
    manifest_file = Path(manifest_path)
    root = manifest_file.parent
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("schema") != "skillrace-timing-pilot-schedule/1":
        raise ValueError("timing pilot schedule schema is invalid")
    cells = manifest.get("cells")
    if not isinstance(cells, list) or len(cells) != 16:
        raise ValueError("timing pilot schedule must contain exactly sixteen cells")
    actual = [cell.get("cell_id") for cell in cells]
    expected = [
        f"{model}/{part}/{name}"
        for model in TIMING_PILOT_MODELS
        for part, names in (
            ("part1", PILOT_PART1_SKILLS),
            ("part2", PILOT_PART2_SCENARIOS),
        )
        for name in names
    ]
    if actual != expected:
        raise ValueError("timing pilot cells do not match the frozen order")
    output_roots: list[Path] = []
    for cell in cells:
        config_path = root / cell["config_path"]
        if file_hash(config_path) != cell.get("config_hash"):
            raise ValueError(f"config hash mismatch for {cell['cell_id']}")
        config = load_config(config_path)
        if (
            config.provider != "lab"
            or config.model_id != cell.get("model_id")
            or config.model_id not in TIMING_PILOT_MODELS
            or config.methods != ("random", "verigrey", "skillrace")
            or config.iteration_budget != 2
            or config.replicate_count != 1
            or config.heldout_repetitions != 1
            or config.timeouts["pi"] != 600
            or not config.live
        ):
            raise ValueError(f"timing pilot config mismatch for {cell['cell_id']}")
        output_roots.append(config.output_root)
        if cell["part"] == "part1":
            if tree_hash(repo / cell["s0_directory"]) != cell.get("s0_tree_hash"):
                raise ValueError(f"S0 hash mismatch for {cell['cell_id']}")
            inputs = (
                ("s0_receipt", "s0_receipt_hash"),
                ("properties", "properties_hash"),
            )
        else:
            inputs = (
                ("scenario", "scenario_hash"),
                ("properties", "properties_hash"),
            )
        for path_name, hash_name in inputs:
            if file_hash(repo / cell[path_name]) != cell.get(hash_name):
                raise ValueError(f"input hash mismatch for {cell['cell_id']}")
        for path, digest in zip(
            cell.get("heldout_tests", []),
            cell.get("heldout_test_hashes", []),
            strict=True,
        ):
            if file_hash(repo / path) != digest:
                raise ValueError(f"held-out hash mismatch for {cell['cell_id']}")
    if len(set(output_roots)) != 16:
        raise ValueError("timing pilot output roots must be unique")
    return len(cells)


def verify_pilot_schedule(repo_root: str | Path, manifest_path: str | Path) -> int:
    repo = Path(repo_root)
    manifest_file = Path(manifest_path)
    root = manifest_file.parent
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("schema") != "skillrace-pilot-schedule/1":
        raise ValueError("pilot schedule schema is invalid")
    cells = manifest.get("cells")
    if not isinstance(cells, list) or len(cells) != 8:
        raise ValueError("pilot schedule must contain exactly eight cells")
    expected = [f"part1/{name}" for name in PILOT_PART1_SKILLS] + [
        f"part2/{name}" for name in PILOT_PART2_SCENARIOS
    ]
    if [cell.get("cell_id") for cell in cells] != expected:
        raise ValueError("pilot cells do not match the approved order")

    output_roots: list[Path] = []
    for cell in cells:
        config_path = root / cell["config_path"]
        if file_hash(config_path) != cell.get("config_hash"):
            raise ValueError(f"config hash mismatch for {cell['cell_id']}")
        config = load_config(config_path)
        if (
            config.provider != "lab"
            or config.model_id != "deepseek-v4-flash"
            or config.methods != ("random", "verigrey", "skillrace")
            or config.iteration_budget != 2
            or config.replicate_count != 1
            or config.heldout_repetitions != 1
            or not config.live
        ):
            raise ValueError(f"pilot config values mismatch for {cell['cell_id']}")
        output_roots.append(config.output_root)
        if cell["part"] == "part1":
            if tree_hash(repo / cell["s0_directory"]) != cell.get("s0_tree_hash"):
                raise ValueError(f"S0 hash mismatch for {cell['cell_id']}")
            for name, hash_name in (
                ("s0_receipt", "s0_receipt_hash"),
                ("properties", "properties_hash"),
            ):
                if file_hash(repo / cell[name]) != cell.get(hash_name):
                    raise ValueError(f"input hash mismatch for {cell['cell_id']}")
        else:
            if file_hash(repo / cell["scenario"]) != cell.get("scenario_hash"):
                raise ValueError(f"scenario hash mismatch for {cell['cell_id']}")
            for path, digest in zip(
                cell["heldout_tests"], cell["heldout_test_hashes"], strict=True
            ):
                if file_hash(repo / path) != digest:
                    raise ValueError(f"held-out hash mismatch for {cell['cell_id']}")
    if len(set(output_roots)) != 8:
        raise ValueError("pilot output roots must be unique")
    return len(cells)
