#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
from intervals import merge
assert merge([[1,2],[1,2],[1,2]])==[[1,2]], merge([[1,2],[1,2],[1,2]])
print('ok')
PY
