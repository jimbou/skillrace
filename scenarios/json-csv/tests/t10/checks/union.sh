#!/usr/bin/env bash
set -u
cd /workspace
python3 convert.py in.json out.csv >/dev/null 2>&1; [ -f out.csv ] || { echo 'FAIL no out.csv'; exit 1; }
h=$(head -1 out.csv|tr -d '[:space:]'); [ "$h" = 'x,y' ] && echo ok || { echo "FAIL header=$h"; exit 1; }
