#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f convert.py ] || { echo 'FAIL convert.py missing'; exit 1; }
rm -f out.csv
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 convert.py in.json out.csv >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL converter exit=$rc"; cat "$stderr"; exit 1; }
[ ! -s "$stderr" ] || { echo 'FAIL unexpected stderr'; cat "$stderr"; exit 1; }
[ -f out.csv ] || { echo 'FAIL out.csv missing'; exit 1; }
[ -f out.csv ] || { echo 'FAIL no out.csv'; exit 1; }
python3 - <<'PY'
import csv
rows=list(csv.DictReader(open('out.csv')))
assert [r['id'] for r in rows]==['7','8'] and [r['label'] for r in rows]==['x','y']
print('ok')
PY
