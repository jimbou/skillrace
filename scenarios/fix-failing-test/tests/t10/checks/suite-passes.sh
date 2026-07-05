#!/usr/bin/env bash
set -u
cd /workspace
python3 -m pytest -q >/tmp/p 2>&1; [ $? -eq 0 ] && echo ok || { echo 'FAIL suite red'; tail -2 /tmp/p; exit 1; }
