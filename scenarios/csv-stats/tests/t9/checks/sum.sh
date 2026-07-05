#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py sum --column n --file big.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 5050|5050.0) echo ok;; *) echo "FAIL sum=$o"; exit 1;; esac
