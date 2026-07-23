#!/usr/bin/env bash
set -u
cd /workspace
python3 tool.py --help >/dev/null 2>&1 && echo ok || { echo FAIL; exit 1; }
