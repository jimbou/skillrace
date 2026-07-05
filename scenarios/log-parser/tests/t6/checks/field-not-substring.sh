#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 logstat.py app.log 2>&1); rc=$?; [ $rc -eq 0 ] || { echo 'FAIL crashed'; exit 1; }; echo "$o"|grep -q 'INFO=1' && echo "$o"|grep -q 'WARN=1' && echo "$o"|grep -q 'ERROR=0' && echo ok || { echo "FAIL counts: $o"; exit 1; }
