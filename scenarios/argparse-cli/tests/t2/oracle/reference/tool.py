import argparse
import pathlib


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="command", required=True)
p = sub.add_parser("add"); p.add_argument("--a", type=int, required=True); p.add_argument("--b", type=int, required=True)
p = sub.add_parser("greet"); p.add_argument("--name", required=True)
p = sub.add_parser("count"); p.add_argument("--file", required=True)
p = sub.add_parser("scale"); p.add_argument("--factor", type=float, required=True); p.add_argument("--value", type=float, required=True)
p = sub.add_parser("mode"); p.add_argument("--kind", choices=("fast", "slow", "auto"), required=True)
p = sub.add_parser("repeat"); p.add_argument("--text", required=True); p.add_argument("--times", type=positive, required=True)
p = sub.add_parser("sum"); p.add_argument("numbers", type=int, nargs="+")
p = sub.add_parser("flag"); p.add_argument("--verbose", action="store_true")
p = sub.add_parser("slugify"); p.add_argument("name")
p = sub.add_parser("stats"); p.add_argument("--nums", type=int, nargs="+", required=True)
p = sub.add_parser("echo"); p.add_argument("--text", required=True)
args = parser.parse_args()
try:
    if args.command == "add": print(args.a + args.b)
    elif args.command == "greet": print(f"Hello, {args.name}!")
    elif args.command == "count": print(len(pathlib.Path(args.file).read_text().splitlines()))
    elif args.command == "scale": print(args.factor * args.value)
    elif args.command == "mode": print(f"kind={args.kind}")
    elif args.command == "repeat": print("\n".join([args.text] * args.times))
    elif args.command == "sum": print(sum(args.numbers))
    elif args.command == "flag": print(f"verbose={args.verbose}")
    elif args.command == "slugify": print(args.name.lower().replace(" ", "-"))
    elif args.command == "stats": print(f"min={min(args.nums)} max={max(args.nums)} sum={sum(args.nums)}")
    elif args.command == "echo": print(args.text)
except OSError as error:
    parser.error(str(error))
