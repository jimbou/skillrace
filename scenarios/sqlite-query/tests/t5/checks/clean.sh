#!/usr/bin/env bash
set -u
cd /workspace
python3 answer.py >/tmp/o 2>&1; grep -qi 'error\|traceback' /tmp/o && { echo 'FAIL sql error'; exit 1; }; echo ok
