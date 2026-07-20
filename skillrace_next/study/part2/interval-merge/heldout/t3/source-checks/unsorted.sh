#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
from intervals import merge
assert merge([[5,6],[1,2],[3,4]])==[[1,2],[3,4],[5,6]], merge([[5,6],[1,2],[3,4]])
print('ok')
PY
