#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 loadcfg.py empty.ini 2>&1); echo "$o"|grep -qi traceback && { echo 'FAIL leaked traceback'; exit 1; }; echo ok
