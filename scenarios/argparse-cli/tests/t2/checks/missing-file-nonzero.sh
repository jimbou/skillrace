#!/usr/bin/env bash
set -u
cd /workspace
python3 tool.py count --file nope.txt >/tmp/o 2>&1; rc=$?; [ $rc -ne 0 ] || { echo 'FAIL missing file: exit 0'; exit 1; }; grep -qi traceback /tmp/o && { echo 'FAIL leaked traceback'; exit 1; }; echo ok
