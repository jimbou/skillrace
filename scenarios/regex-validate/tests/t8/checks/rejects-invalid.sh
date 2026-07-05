#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
bad=['550e8400-e29b-41d4-a716-44665544000', '550E8400-E29B-41D4-A716-446655440000', 'not-a-uuid', '']
for s in bad: assert v.is_valid(s) is False, ('should reject',s)
print('ok')
PY
