#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f loadcfg.py ] || { echo 'FAIL loadcfg.py missing'; exit 1; }
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 loadcfg.py empty.ini >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -ne 0 ] || { echo "FAIL expected non-zero exit: rc=$rc"; cat "$stderr"; exit 1; }
cat "$stdout" "$stderr" | grep -qi traceback && { echo 'FAIL leaked traceback'; exit 1; }
[ -s "$stderr" ] || { echo 'FAIL expected diagnostic on stderr'; exit 1; }
cat "$stdout" "$stderr" | grep -qi 'server' || { echo 'FAIL diagnostic did not name server'; exit 1; }
echo ok
