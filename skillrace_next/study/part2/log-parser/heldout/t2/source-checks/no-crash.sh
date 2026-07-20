#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f logstat.py ] || { echo 'FAIL logstat.py missing'; exit 1; }
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 logstat.py app.log >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL command exit=$rc"; cat "$stderr"; exit 1; }
[ ! -s "$stderr" ] || { echo 'FAIL unexpected stderr'; cat "$stderr"; exit 1; }
echo ok
