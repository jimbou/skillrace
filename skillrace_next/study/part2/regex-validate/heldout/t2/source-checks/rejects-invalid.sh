#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
bad=['-lead', 'trail-', 'My-Post', 'a--b', 'has space', '', 'a-']
for s in bad: assert v.is_valid(s) is False, ('should reject',s)
print('ok')
PY
