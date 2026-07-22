from pathlib import Path

import pytest

from skillrace_next import cli
from skillrace_next.cli import build_parser


@pytest.mark.parametrize(
    "command",
    ["live-smoke", "part1", "part2", "analyze", "build-study-images"],
)
def test_public_commands_parse(command: str) -> None:
    if command == "analyze":
        argv = [command, "--run", "run-dir"]
    elif command == "build-study-images":
        argv = [command, "--live", "--run-id", "image-run"]
    else:
        argv = [command, "--config", "config.json"]
    if command == "live-smoke":
        argv.extend(["--component", "pi-runtime"])
    parsed = build_parser().parse_args(argv)
    assert parsed.command == command


def test_build_study_images_requires_live() -> None:
    with pytest.raises(ValueError, match="requires explicit --live"):
        cli.main(["build-study-images", "--run-id", "image-run"])


def test_build_study_images_runs_the_frozen_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_builder(*, run_id: str) -> Path:
        calls.append(run_id)
        return tmp_path / "manifest.json"

    monkeypatch.setattr(cli, "build_study_images", fake_builder)

    assert cli.main(
        ["build-study-images", "--live", "--run-id", "image-run"]
    ) == 0
    assert calls == ["image-run"]


def test_internal_stage_is_not_a_command() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["author-checks"])
