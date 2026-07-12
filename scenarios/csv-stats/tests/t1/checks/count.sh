#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f stats.py ] || { echo 'FAIL stats.py missing'; exit 1; }
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 stats.py count --column score --file data.csv >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL valid count exit=$rc"; cat "$stderr"; exit 1; }
[ "$(tr -d '[:space:]' <"$stdout")" = 3 ] || { echo 'FAIL valid count'; exit 1; }
: >"$stdout"; : >"$stderr"
python3 stats.py count --column absent --file data.csv >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -ne 0 ] || { echo 'FAIL absent column exited zero'; exit 1; }
[ ! -s "$stdout" ] || { echo 'FAIL absent column produced result'; exit 1; }
grep -qi 'column' "$stderr" || { echo 'FAIL absent-column diagnostic'; exit 1; }
grep -qi traceback "$stderr" && { echo 'FAIL absent-column traceback'; exit 1; }
echo ok
