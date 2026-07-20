#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f logstat.py ] || { echo 'FAIL logstat.py missing'; exit 1; }
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 logstat.py app.log >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL expected exit 0: rc=$rc"; cat "$stderr"; exit 1; }
[ ! -s "$stderr" ] || { echo 'FAIL unexpected stderr'; cat "$stderr"; exit 1; }
mapfile -t lines <"$stdout"
[ "${#lines[@]}" -eq 3 ] || { echo 'FAIL expected exactly three output lines'; exit 1; }
[ "${lines[0]}" = 'INFO=0' ] && [ "${lines[1]}" = 'WARN=2' ] && [ "${lines[2]}" = 'ERROR=0' ] || { printf 'FAIL output=%s
' "${lines[*]}"; exit 1; }
echo ok
