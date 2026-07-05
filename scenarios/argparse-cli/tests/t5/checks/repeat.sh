#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 tool.py repeat --text hi --times 3 2>/dev/null|grep -c '^hi$'); [ "$o" = 3 ] && echo ok || { echo "FAIL lines=$o"; exit 1; }
