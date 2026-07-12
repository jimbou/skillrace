import argparse
import csv
import sys


parser = argparse.ArgumentParser()
parser.add_argument("op", choices=("sum", "mean", "count", "min", "max"))
parser.add_argument("--column", required=True)
parser.add_argument("--file", required=True)
args = parser.parse_args()
try:
    stream = open(args.file, newline="", encoding="utf-8")
except OSError as error:
    parser.error(f"file {args.file}: {error.strerror or error}")
with stream:
    reader = csv.DictReader(stream)
    if reader.fieldnames is None or args.column not in reader.fieldnames:
        parser.error(f"column {args.column} is absent")
    values = []
    for row in reader:
        raw = (row.get(args.column) or "").strip()
        if not raw:
            continue
        try:
            values.append(float(raw))
        except ValueError:
            continue
if args.op == "count": result = len(values)
elif not values: result = 0
elif args.op == "sum": result = sum(values)
elif args.op == "mean": result = sum(values) / len(values)
elif args.op == "min": result = min(values)
else: result = max(values)
print(int(result) if isinstance(result, float) and result.is_integer() else result)
