#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 tool.py sum 1 2 3 2>/dev/null|tr -d '[:space:]'); [ "$o" = '6' ] && echo ok || { echo "FAIL sum=$o"; exit 1; }
