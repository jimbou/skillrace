#!/usr/bin/env bash
set -u
cd /workspace
python3 stats.py mean --column val --file messy.csv >/dev/null 2>&1; [ $? -le 1 ] && echo ok || { echo 'FAIL crashed'; exit 1; }
