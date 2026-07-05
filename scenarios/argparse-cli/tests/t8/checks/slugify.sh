#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 tool.py slugify 'Hello World' 2>/dev/null|tr -d '[:space:]'); [ "$o" = 'hello-world' ] && echo ok || { echo "FAIL slug=$o"; exit 1; }
