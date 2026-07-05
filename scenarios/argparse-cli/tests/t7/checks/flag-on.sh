#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 tool.py flag --verbose 2>/dev/null); echo "$o"|grep -q 'verbose=True' && echo ok || { echo "FAIL on=$o"; exit 1; }
