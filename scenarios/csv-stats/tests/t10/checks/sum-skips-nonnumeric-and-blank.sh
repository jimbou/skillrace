#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py sum --column val --file messy.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 30|30.0) echo ok;; *) echo "FAIL sum=$o (should skip N/A and blank line)"; exit 1;; esac
