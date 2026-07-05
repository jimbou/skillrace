#!/usr/bin/env bash
set -u
cd /workspace
python3 convert.py in.json out.csv >/dev/null 2>&1; [ -f out.csv ] || { echo 'FAIL no out.csv'; exit 1; }
python3 - <<'PY'
import csv
rows=list(csv.DictReader(open('out.csv')))
assert len(rows)==2
assert rows[1]['a']=='3' and rows[1]['b']=='' and rows[1]['c']=='4', rows[1]
print('ok')
PY
