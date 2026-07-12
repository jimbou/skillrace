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
assert len(rows)==2
assert rows[1]['a']=='3' and rows[1]['b']=='' and rows[1]['c']=='4', rows[1]
print('ok')
PY
