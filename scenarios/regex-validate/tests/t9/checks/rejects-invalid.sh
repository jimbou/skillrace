#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
bad=['1.2', '1.2.3.4', '01.2.3', 'v1.2.3', '1.2.x', '']
for s in bad: assert v.is_valid(s) is False, ('should reject',s)
print('ok')
PY
