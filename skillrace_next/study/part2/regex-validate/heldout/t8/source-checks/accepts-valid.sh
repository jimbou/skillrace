#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
ok=['550e8400-e29b-41d4-a716-446655440000']
for s in ok: assert v.is_valid(s) is True, ('should accept',s)
print('ok')
PY
