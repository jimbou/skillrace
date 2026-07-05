#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py max --column amount --file sales.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 100|100.0) echo ok;; *) echo "FAIL max=$o (quoted comma broke parsing?)"; exit 1;; esac
