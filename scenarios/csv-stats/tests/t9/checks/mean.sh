#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py mean --column n --file big.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 50.5) echo ok;; *) echo "FAIL mean=$o"; exit 1;; esac
