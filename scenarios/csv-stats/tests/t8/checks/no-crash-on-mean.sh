#!/usr/bin/env bash
set -u
cd /workspace
err=$(python3 stats.py mean --column b --file empty.csv 2>&1 >/dev/null); echo "$err"|grep -qi traceback && { echo 'FAIL traceback on empty'; exit 1; }; echo ok
