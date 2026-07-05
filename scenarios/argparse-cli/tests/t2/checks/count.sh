#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 tool.py count --file sample.txt 2>/dev/null|tr -d '[:space:]'); [ "$o" = '3' ] && echo ok || { echo "FAIL count=$o"; exit 1; }
