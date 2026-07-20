#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
from intervals import merge
assert merge([[4,7]])==[[4,7]]
print('ok')
PY
