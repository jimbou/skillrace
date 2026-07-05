#!/usr/bin/env bash
set -u
cd /workspace
start=$(date +%s); python3 stats.py sum --column n --file big.csv >/dev/null 2>&1; e=$(( $(date +%s) - start )); [ "$e" -le 10 ] && echo ok || { echo "FAIL slow ${e}s"; exit 1; }
