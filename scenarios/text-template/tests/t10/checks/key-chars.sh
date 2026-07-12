#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f render.py ] || { echo 'FAIL render.py missing'; exit 1; }
rm -f out.txt
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 render.py tmpl.txt data.json out.txt >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL renderer exit=$rc"; cat "$stderr"; exit 1; }
[ ! -s "$stderr" ] || { echo 'FAIL unexpected stderr'; cat "$stderr"; exit 1; }
[ -f out.txt ] || { echo 'FAIL out.txt missing'; exit 1; }
got=$(cat out.txt)
[ "$got" = 'Ada_ok' ] || { echo "FAIL got=[$got]"; exit 1; }
echo ok
