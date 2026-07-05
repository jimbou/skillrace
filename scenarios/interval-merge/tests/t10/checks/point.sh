#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
from intervals import merge
assert merge([[2,2],[2,5]])==[[2,5]], merge([[2,2],[2,5]])
print('ok')
PY
