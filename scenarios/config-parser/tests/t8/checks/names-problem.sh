#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 loadcfg.py float.ini 2>&1); echo "$o"|grep -qi 'port' && echo ok || { echo "FAIL should mention port: $o"; exit 1; }
