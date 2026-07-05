#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
ok=['0.0.0', '1.2.3', '10.20.30']
for s in ok: assert v.is_valid(s) is True, ('should accept',s)
print('ok')
PY
