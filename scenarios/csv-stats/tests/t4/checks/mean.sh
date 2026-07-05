#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py mean --column delta --file ledger.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 16.66*|16.67) echo ok;; *) echo "FAIL mean=$o"; exit 1;; esac
