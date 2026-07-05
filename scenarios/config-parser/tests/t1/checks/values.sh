#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 loadcfg.py app.ini 2>/dev/null); echo "$o"|grep -q localhost && echo "$o"|grep -q 8080 && echo ok || { echo "FAIL=$o"; exit 1; }
