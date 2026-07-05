#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
from intervals import merge
assert merge([[1,3],[2,6],[8,10],[15,18]])==[[1,6],[8,10],[15,18]], merge([[1,3],[2,6],[8,10],[15,18]])
print('ok')
PY
