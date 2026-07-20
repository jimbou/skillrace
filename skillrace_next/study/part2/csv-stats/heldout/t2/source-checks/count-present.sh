#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f stats.py ] || { echo 'FAIL stats.py missing'; exit 1; }
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 stats.py count --column amount --file sales.csv >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL valid count exit=$rc"; cat "$stderr"; exit 1; }
[ "$(tr -d '[:space:]' <"$stdout")" = 2 ] || { echo 'FAIL valid count'; exit 1; }
: >"$stdout"; : >"$stderr"
python3 stats.py count --column amount --file missing.csv >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -ne 0 ] || { echo 'FAIL missing file exited zero'; exit 1; }
[ ! -s "$stdout" ] || { echo 'FAIL missing file produced result'; exit 1; }
grep -Eqi 'file|missing.csv' "$stderr" || { echo 'FAIL missing-file diagnostic'; exit 1; }
grep -qi traceback "$stderr" && { echo 'FAIL missing-file traceback'; exit 1; }
echo ok
