#!/usr/bin/env bash
set -u
cd /workspace
o=$(python3 tool.py mode --kind fast 2>/dev/null); echo "$o"|grep -q 'kind=fast' && echo ok || { echo "FAIL kind=$o"; exit 1; }
