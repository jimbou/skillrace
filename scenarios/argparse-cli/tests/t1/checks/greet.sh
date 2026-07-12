#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f tool.py ] || { echo 'FAIL tool.py missing'; exit 1; }
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 tool.py greet --name Sam >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL expected exit 0: rc=$rc"; cat "$stderr"; exit 1; }
[ ! -s "$stderr" ] || { echo 'FAIL unexpected stderr'; cat "$stderr"; exit 1; }
got=$(cat "$stdout")
[ "$got" = 'Hello, Sam!' ] || { echo "FAIL output=[$got]"; exit 1; }
echo ok
