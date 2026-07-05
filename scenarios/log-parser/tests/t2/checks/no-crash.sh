#!/usr/bin/env bash
set -u
cd /workspace
python3 logstat.py app.log >/dev/null 2>&1; [ $? -eq 0 ] && echo ok || { echo 'FAIL crashed'; exit 1; }
