#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 loadcfg.py empty.ini 2>&1); echo "$o"|grep -qi 'server' && echo ok || { echo "FAIL should mention server: $o"; exit 1; }
