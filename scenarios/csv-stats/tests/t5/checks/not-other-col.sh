#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f stats.py ] || { echo 'FAIL stats.py missing'; exit 1; }
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 stats.py sum --column price --file inv.csv >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL expected exit 0: rc=$rc"; cat "$stderr"; exit 1; }
[ ! -s "$stderr" ] || { echo 'FAIL unexpected stderr'; cat "$stderr"; exit 1; }
got=$(tr -d '[:space:]' <"$stdout")
case "$got" in 14.49) :;; *) echo "FAIL output=[$got]"; exit 1;; esac
echo ok
