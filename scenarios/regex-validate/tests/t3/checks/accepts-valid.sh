#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
ok=['a@b.co', 'john.doe@ex.com', 'x_y+z@sub-dom.io']
for s in ok: assert v.is_valid(s) is True, ('should accept',s)
print('ok')
PY
