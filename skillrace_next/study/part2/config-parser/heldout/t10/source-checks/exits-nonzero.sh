#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f loadcfg.py ] || { echo 'FAIL loadcfg.py missing'; exit 1; }
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 loadcfg.py neg.ini >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -ne 0 ] || { echo 'FAIL accepted invalid config'; exit 1; }
[ -s "$stderr" ] || { echo 'FAIL expected diagnostic on stderr'; exit 1; }
echo ok
