#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
bad=['2024-13-01', '2024-00-10', '2024-1-1', '24-01-01', '2024/01/01', '2024-01-32']
for s in bad: assert v.is_valid(s) is False, ('should reject',s)
print('ok')
PY
