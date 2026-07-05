#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py count --column v --file ws.csv 2>/dev/null|tr -d '[:space:]'); [ "$o" = 3 ] && echo ok || { echo "FAIL count=$o"; exit 1; }
