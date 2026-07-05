#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
ok=['a', 'my-post-2', 'abc123', 'x-y-z']
for s in ok: assert v.is_valid(s) is True, ('should accept',s)
print('ok')
PY
