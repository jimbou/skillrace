#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py sum --column price --file inv.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 14.49) echo ok;; *) echo "FAIL price sum=$o (column selection wrong?)"; exit 1;; esac
