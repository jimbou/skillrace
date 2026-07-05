#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py min --column temp --file temps.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in -3|-3.0) echo ok;; *) echo "FAIL min=$o"; exit 1;; esac
