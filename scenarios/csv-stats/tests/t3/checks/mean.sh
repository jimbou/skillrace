#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py mean --column temp --file temps.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 6|6.0) echo ok;; *) echo "FAIL mean=$o"; exit 1;; esac
