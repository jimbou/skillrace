#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
ok=['00:00', '09:05', '23:59', '13:30']
for s in ok: assert v.is_valid(s) is True, ('should accept',s)
print('ok')
PY
