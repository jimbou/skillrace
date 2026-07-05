#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 tool.py scale --factor 2.5 --value 4 2>/dev/null|tr -d '[:space:]'); [ "$o" = '10.0' ] && echo ok || { echo "FAIL scale=$o"; exit 1; }
