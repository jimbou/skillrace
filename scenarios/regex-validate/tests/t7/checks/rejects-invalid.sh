#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
bad=['123-456-7890', '(12) 456-7890', '(123)456-7890', '(123) 4567-890', '']
for s in bad: assert v.is_valid(s) is False, ('should reject',s)
print('ok')
PY
