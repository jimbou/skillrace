#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py mean --column y --file one.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 42|42.0) echo ok;; *) echo "FAIL mean=$o"; exit 1;; esac
