#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 loadcfg.py spaced.ini 2>/dev/null); echo "$o"|grep -q 'host=host.example' && echo "$o"|grep -q 'port=443' && echo ok || { echo "FAIL=$o"; exit 1; }
