#!/usr/bin/env bash
set -u
cd /workspace
python3 tool.py sum >/tmp/o 2>&1; rc=$?; [ $rc -ne 0 ] || { echo 'FAIL no numbers: exit 0'; exit 1; }; grep -qi traceback /tmp/o && { echo 'FAIL leaked traceback'; exit 1; }; echo ok
