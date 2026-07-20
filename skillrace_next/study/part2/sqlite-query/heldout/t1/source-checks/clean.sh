#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f answer.py ] || { echo 'FAIL answer.py missing'; exit 1; }
backup=$(mktemp); stdout=$(mktemp); stderr=$(mktemp)
cp data.db "$backup"
trap 'cp "$backup" data.db; rm -f "$backup" "$stdout" "$stderr"' EXIT
python3 - <<'PYORACLE'
import sqlite3
connection = sqlite3.connect('data.db')
connection.execute("INSERT INTO sales VALUES('z',7,1.0,'N')")
connection.commit()
PYORACLE
python3 answer.py >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL command exit=$rc"; cat "$stderr"; exit 1; }
grep -Eqi 'error|traceback' "$stdout" "$stderr" && { echo 'FAIL leaked SQL error'; exit 1; }
got=$(tr -d '[:space:]' <"$stdout")
case "$got" in 27|27.0) echo ok;; *) echo "FAIL query-specific probe output=[$got]"; exit 1;; esac
