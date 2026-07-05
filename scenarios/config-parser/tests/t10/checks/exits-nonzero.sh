#!/usr/bin/env bash
set -u
cd /workspace
python3 loadcfg.py neg.ini >/dev/null 2>&1; [ $? -ne 0 ] && echo ok || { echo 'FAIL accepted bad config'; exit 1; }
