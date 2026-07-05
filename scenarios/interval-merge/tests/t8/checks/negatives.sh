#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
from intervals import merge
assert merge([[-5,-1],[-2,0],[3,4]])==[[-5,0],[3,4]], merge([[-5,-1],[-2,0],[3,4]])
print('ok')
PY
