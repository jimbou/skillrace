#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
from intervals import merge
assert merge([[1,2],[2,3]])==[[1,3]], merge([[1,2],[2,3]])
print('ok')
PY
