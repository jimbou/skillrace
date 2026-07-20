#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
ok=['(123) 456-7890', '(000) 000-0000']
for s in ok: assert v.is_valid(s) is True, ('should accept',s)
print('ok')
PY
