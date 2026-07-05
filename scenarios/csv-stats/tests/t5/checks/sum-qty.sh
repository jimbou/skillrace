#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py sum --column qty --file inv.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 15|15.0) echo ok;; *) echo "FAIL qty sum=$o"; exit 1;; esac
