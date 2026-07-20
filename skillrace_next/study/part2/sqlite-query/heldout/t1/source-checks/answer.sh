#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f answer.py ] || { echo 'FAIL answer.py missing'; exit 1; }
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 answer.py >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL expected exit 0: rc=$rc"; cat "$stderr"; exit 1; }
[ ! -s "$stderr" ] || { echo 'FAIL unexpected stderr'; cat "$stderr"; exit 1; }
got=$(tr -d '[:space:]' <"$stdout")
case "$got" in 20|20.0) :;; *) echo "FAIL output=[$got]"; exit 1;; esac
echo ok
