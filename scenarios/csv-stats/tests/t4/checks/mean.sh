#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f stats.py ] || { echo 'FAIL stats.py missing'; exit 1; }
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 stats.py mean --column delta --file ledger.csv >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL expected exit 0: rc=$rc"; cat "$stderr"; exit 1; }
[ ! -s "$stderr" ] || { echo 'FAIL unexpected stderr'; cat "$stderr"; exit 1; }
python3 - "$stdout" <<'PYORACLE'
import pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text().strip()
assert text and "\n" not in text
assert abs(float(text) - (50 / 3)) <= 0.01, text
PYORACLE
echo ok
