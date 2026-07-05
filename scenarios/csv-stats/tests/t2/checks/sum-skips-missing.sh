#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py sum --column amount --file sales.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 150|150.0) echo ok;; *) echo "FAIL sum=$o"; exit 1;; esac
