#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 tool.py sum 42 2>/dev/null|tr -d '[:space:]'); [ "$o" = '42' ] && echo ok || { echo "FAIL single=$o"; exit 1; }
