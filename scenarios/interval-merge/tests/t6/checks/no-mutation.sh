#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
from intervals import merge
inp=[[1,3],[2,6]]; snap=[list(x) for x in inp]; merge(inp); assert inp==snap, inp
print('ok')
PY
