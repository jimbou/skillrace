#!/usr/bin/env bash
set -u
cd /workspace
python3 tool.py echo --text hi >/tmp/o 2>&1; [ $? -eq 0 ] && grep -q '^hi$' /tmp/o && echo ok || { echo FAIL; exit 1; }
