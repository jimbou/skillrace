#!/usr/bin/env bash
set -u
cd /workspace
python3 tool.py mode --kind sideways >/tmp/o 2>&1; rc=$?; [ $rc -ne 0 ] || { echo 'FAIL invalid choice: exit 0'; exit 1; }; grep -qi traceback /tmp/o && { echo 'FAIL leaked traceback'; exit 1; }; echo ok
