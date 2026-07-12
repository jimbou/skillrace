#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f loadcfg.py ] || { echo 'FAIL loadcfg.py missing'; exit 1; }
stdout=$(mktemp); stderr=$(mktemp)
trap 'rm -f "$stdout" "$stderr"' EXIT
python3 loadcfg.py ok.ini >"$stdout" 2>"$stderr"
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL expected exit 0: rc=$rc"; cat "$stderr"; exit 1; }
read -r host_field port_field extra <"$stdout"
[ -z "${extra:-}" ] && [ "$port_field" = 'port=5432' ] || { echo 'FAIL port field'; exit 1; }
echo ok
