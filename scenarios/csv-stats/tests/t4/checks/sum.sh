#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 stats.py sum --column delta --file ledger.csv 2>/dev/null|tr -d '[:space:]'); case "$o" in 50|50.0) echo ok;; *) echo "FAIL sum=$o"; exit 1;; esac
