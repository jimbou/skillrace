#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
bad=['24:00', '23:60', '9:05', '09:5', '7am', '', ' 1:2', '23:59 ']
for s in bad: assert v.is_valid(s) is False, ('should reject',s)
print('ok')
PY
