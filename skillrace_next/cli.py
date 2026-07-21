import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

from .config import freeze_config, load_config
from .records import ExperimentConfig
from .storage import atomic_write_json


_LIVE_COMPONENTS = (
    "pi-runtime",
    "task-runner",
    "test-proposer",
    "codex-verifier",
    "check-executor",
    "episode-creator",
    "tree-merge",
    "skillrace-proposal",
    "verigrey",
    "skill-generation",
    "patcher",
    "exact-replay",
    "part1",
    "part2",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m skillrace_next")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("live-smoke", "part1", "part2", "analyze"):
        command = sub.add_parser(name)
        if name == "analyze":
            command.add_argument("--run", required=True)
        else:
            command.add_argument("--config", required=True)
            command.add_argument("--live", action="store_true")
            if name == "live-smoke":
                command.add_argument("--component", choices=_LIVE_COMPONENTS, required=True)
            elif name == "part1":
                command.add_argument("--s0-dir")
                command.add_argument("--s0-receipt")
                command.add_argument("--skill-id")
                command.add_argument("--properties")
            elif name == "part2":
                command.add_argument("--scenario")
                command.add_argument("--properties")
                command.add_argument("--heldout-test", action="append", default=[])
    return parser


def _live_contract_path(component: str) -> str:
    if component == "pi-runtime":
        return "tests_next/live/test_pi_runtime_live.py"
    if component == "task-runner":
        return "tests_next/live/test_task_runner_live.py"
    if component == "test-proposer":
        return "tests_next/live/test_test_proposer_live.py"
    if component == "codex-verifier":
        return "tests_next/live/test_codex_verifier_live.py"
    if component == "check-executor":
        return "tests_next/live/test_check_executor_live.py"
    if component == "episode-creator":
        return "tests_next/live/test_episode_creator_live.py"
    if component == "tree-merge":
        return "tests_next/live/test_tree_merge_live.py"
    if component == "skillrace-proposal":
        return "tests_next/live/test_skillrace_proposal_live.py"
    if component == "verigrey":
        return "tests_next/live/test_verigrey_live.py"
    if component == "skill-generation":
        return "tests_next/live/test_skill_generation_live.py"
    if component == "patcher":
        return "tests_next/live/test_patcher_live.py"
    if component == "exact-replay":
        return "tests_next/live/test_exact_replay_live.py"
    if component == "part1":
        return "tests_next/live/test_part1_tiny_live.py"
    if component == "part2":
        return "tests_next/live/test_part2_tiny_live.py"
    raise ValueError(f"unknown live component: {component}")


def _run_live_contract(component: str) -> int:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            _live_contract_path(component),
            "--live",
            "-v",
            "-s",
        ],
        check=False,
    )
    return completed.returncode


def _freeze_command(
    config_path: str,
    command: str,
    *,
    live: bool,
    scenario_path: str | None = None,
) -> ExperimentConfig:
    config = load_config(config_path)
    if command in {"part1", "part2"} and config.part != command:
        raise ValueError(f"{command} command requires a {command} config")
    if config.live != live:
        print(
            "warning: --live overrides config "
            f"live={str(config.live).lower()} with {str(live).lower()}",
            file=sys.stderr,
        )
        config = replace(config, live=live)
    if command == "part2" and scenario_path is not None:
        effective_scenario = Path(scenario_path)
        if config.scenario_path != effective_scenario:
            print(
                "warning: --scenario overrides config "
                f"scenario_path={config.scenario_path} with {effective_scenario}",
                file=sys.stderr,
            )
            config = replace(config, scenario_path=effective_scenario)
    freeze_config(config, config.output_root)
    return config


def run_part1_campaign(
    config: ExperimentConfig,
    s0_dir: Path,
    s0_receipt: Path,
    skill_id: str,
    property_path: Path,
    output: Path,
) -> dict[str, object]:
    from .pipeline.campaigns import run_part1_campaign as run

    return run(config, s0_dir, s0_receipt, skill_id, property_path, output)


def run_part2_campaign(
    config: ExperimentConfig,
    scenario_path: Path,
    property_path: Path,
    heldout_paths: list[Path],
    output: Path,
) -> dict[str, object]:
    from .pipeline.campaigns import run_part2_campaign as run

    return run(config, scenario_path, property_path, heldout_paths, output)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "analyze":
        run = Path(args.run)
        source = json.loads((run / "summary.json").read_text(encoding="utf-8"))
        if not isinstance(source, dict) or not isinstance(source.get("summary"), dict):
            raise ValueError("run summary is invalid")
        atomic_write_json(
            run / "analysis.json",
            {
                "schema": "skillrace-analysis/1",
                "source_schema": source.get("schema"),
                "summary": source["summary"],
            },
        )
        return 0

    config = _freeze_command(
        args.config,
        args.command,
        live=args.live,
        scenario_path=getattr(args, "scenario", None),
    )
    output = config.output_root
    if args.command == "live-smoke":
        if not args.live:
            raise ValueError("live-smoke requires explicit --live")
        status = _run_live_contract(args.component)
        atomic_write_json(
            output / "command.json",
            {
                "schema": "skillrace-command/1",
                "command": args.command,
                "component": args.component,
                "live": True,
                "status": "passed" if status == 0 else "failed",
            },
        )
        return status

    if args.live:
        try:
            if args.command == "part1":
                required = {
                    "--s0-dir": args.s0_dir,
                    "--s0-receipt": args.s0_receipt,
                    "--skill-id": args.skill_id,
                    "--properties": args.properties,
                }
                missing = [name for name, value in required.items() if not value]
                if missing:
                    raise ValueError("part1 --live requires " + ", ".join(missing))
            else:
                if not args.scenario or not args.properties or not args.heldout_test:
                    raise ValueError(
                        "part2 --live requires --scenario, --properties, and at least "
                        "one --heldout-test"
                    )
            for replicate_number in range(1, config.replicate_count + 1):
                replicate_root = (
                    output / "replicates" / f"{replicate_number:04d}"
                )
                replicate_config = replace(config, output_root=replicate_root)
                if args.command == "part1":
                    run_part1_campaign(
                        replicate_config,
                        Path(args.s0_dir),
                        Path(args.s0_receipt),
                        args.skill_id,
                        Path(args.properties),
                        replicate_root / "campaign",
                    )
                else:
                    run_part2_campaign(
                        replicate_config,
                        Path(args.scenario),
                        Path(args.properties),
                        [Path(path) for path in args.heldout_test],
                        replicate_root / "campaign",
                    )
        except Exception:
            atomic_write_json(
                output / "command.json",
                {
                    "schema": "skillrace-command/1",
                    "command": args.command,
                    "live": True,
                    "status": "failed",
                },
            )
            raise
        atomic_write_json(
            output / "command.json",
            {
                "schema": "skillrace-command/1",
                "command": args.command,
                "live": True,
                "status": "completed",
            },
        )
        return 0

    atomic_write_json(
        output / "command.json",
        {
            "schema": "skillrace-command/1",
            "command": args.command,
            "live": False,
            "status": "config_frozen",
        },
    )
    return 0
