#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
ok=['x', '_hidden', 'fooBar2', 'a_b_c']
for s in ok: assert v.is_valid(s) is True, ('should accept',s)
print('ok')
PY
