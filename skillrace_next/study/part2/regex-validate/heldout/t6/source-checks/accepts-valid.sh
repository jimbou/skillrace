#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
ok=['#fff', '#FFFFFF', '#a1b2c3', '#000']
for s in ok: assert v.is_valid(s) is True, ('should accept',s)
print('ok')
PY
