#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 tool.py flag 2>/dev/null); echo "$o"|grep -q 'verbose=False' && echo ok || { echo "FAIL off=$o"; exit 1; }
