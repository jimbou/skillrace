#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 loadcfg.py nohost.ini 2>&1); echo "$o"|grep -qi 'host' && echo ok || { echo "FAIL should mention host: $o"; exit 1; }
