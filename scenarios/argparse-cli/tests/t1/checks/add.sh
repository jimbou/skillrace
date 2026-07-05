#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 tool.py add --a 2 --b 3 2>/dev/null|tr -d '[:space:]'); [ "$o" = '5' ] && echo ok || { echo "FAIL add=$o"; exit 1; }
