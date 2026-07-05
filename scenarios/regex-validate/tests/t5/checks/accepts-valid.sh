#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
ok=['2024-01-01', '1999-12-31', '2020-06-15']
for s in ok: assert v.is_valid(s) is True, ('should accept',s)
print('ok')
PY
