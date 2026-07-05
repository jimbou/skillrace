#!/usr/bin/env bash
set -u
cd /workspace
python3 loadcfg.py ok.ini 2>/dev/null|grep -q 5432 && echo ok || { echo FAIL; exit 1; }
