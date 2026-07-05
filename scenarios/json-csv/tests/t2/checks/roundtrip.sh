#!/usr/bin/env bash
set -u
cd /workspace
python3 convert.py in.json out.csv >/dev/null 2>&1; [ -f out.csv ] || { echo 'FAIL no out.csv'; exit 1; }
python3 - <<'PY'
import csv
rows=list(csv.DictReader(open('out.csv')))
assert rows[0]['name']=='Acme, Inc.' and rows[0]['note']=='say "hi"', rows[0]
print('ok')
PY
