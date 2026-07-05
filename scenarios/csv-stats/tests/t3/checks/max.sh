#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py max --column temp --file temps.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 12.5) echo ok;; *) echo "FAIL max=$o"; exit 1;; esac
