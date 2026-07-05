#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
from intervals import merge
assert merge([[1,2],[4,5]])==[[1,2],[4,5]], merge([[1,2],[4,5]])
print('ok')
PY
