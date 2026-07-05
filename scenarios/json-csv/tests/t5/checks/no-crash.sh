#!/usr/bin/env bash
set -u
cd /workspace
python3 convert.py in.json out.csv >/tmp/o 2>&1; grep -qi traceback /tmp/o && { echo FAIL; exit 1; }; echo ok
