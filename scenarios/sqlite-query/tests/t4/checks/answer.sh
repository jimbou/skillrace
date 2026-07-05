#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 answer.py 2>/dev/null|tr -d '[:space:]'); case "$o" in 4|4.0) echo ok;; *) echo "FAIL got=$o"; exit 1;; esac
