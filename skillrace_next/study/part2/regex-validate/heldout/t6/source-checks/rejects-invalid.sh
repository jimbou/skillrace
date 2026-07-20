#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
bad=['fff', '#ff', '#gggggg', '#12345', '#1234567', '']
for s in bad: assert v.is_valid(s) is False, ('should reject',s)
print('ok')
PY
