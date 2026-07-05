#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 tool.py greet --name Sam 2>/dev/null); echo "$o"|grep -q 'Hello, Sam!' && echo ok || { echo "FAIL greet=$o"; exit 1; }
