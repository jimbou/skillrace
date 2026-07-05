#!/usr/bin/env bash
set -u
cd /workspace
python3 tool.py --help >/tmp/h 2>&1 || { echo 'FAIL help exit'; exit 1; }; grep -q count /tmp/h && echo ok || { echo 'FAIL help omits count'; exit 1; }
