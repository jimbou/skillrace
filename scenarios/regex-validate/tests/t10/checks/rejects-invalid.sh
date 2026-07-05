#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
bad=['2fast', 'has space', 'with-dash', '', 'é name']
for s in bad: assert v.is_valid(s) is False, ('should reject',s)
print('ok')
PY
