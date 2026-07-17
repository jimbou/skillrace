import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m skillrace_next")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("live-smoke", "part1", "part2", "analyze"):
        command = sub.add_parser(name)
        if name == "analyze":
            command.add_argument("--run")
        else:
            command.add_argument("--config")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
