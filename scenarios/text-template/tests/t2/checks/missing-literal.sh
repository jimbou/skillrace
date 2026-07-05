#!/usr/bin/env bash
set -u
cd /workspace
python3 render.py tmpl.txt data.json out.txt >/dev/null 2>&1; got=$(cat out.txt 2>/dev/null); [ "$got" = 'A=1 B={{missing}} C=3' ] && echo ok || { echo "FAIL got=[$got]"; exit 1; }
