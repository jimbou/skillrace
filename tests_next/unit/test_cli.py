import pytest

from skillrace_next.cli import build_parser


@pytest.mark.parametrize("command", ["live-smoke", "part1", "part2", "analyze"])
def test_only_four_public_commands_parse(command: str) -> None:
    option = "--run" if command == "analyze" else "--config"
    value = "run-dir" if command == "analyze" else "config.json"
    parsed = build_parser().parse_args([command, option, value])
    assert parsed.command == command


def test_internal_stage_is_not_a_command() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["author-checks"])
