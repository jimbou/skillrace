#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 tool.py stats --nums 4 1 7 2>/dev/null); echo "$o"|grep -q 'min=1' && echo "$o"|grep -q 'max=7' && echo "$o"|grep -q 'sum=12' && echo ok || { echo "FAIL=$o"; exit 1; }
