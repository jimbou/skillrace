#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py mean --column score --file data.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 20|20.0|20.00) echo ok;; *) echo "FAIL mean=$o"; exit 1;; esac
