#!/usr/bin/env bash
set -u
cd /workspace
python3 convert.py in.json out.csv >/dev/null 2>&1; [ -f out.csv ] || { echo 'FAIL no out.csv'; exit 1; }
python3 - <<'PY'
import csv
rows=list(csv.DictReader(open('out.csv')))
assert [r['id'] for r in rows]==['7','8'] and [r['label'] for r in rows]==['x','y']
print('ok')
PY
