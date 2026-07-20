#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
bad=['a@b', '@b.co', 'a b@c.co', 'a@b.', 'a@.co', '']
for s in bad: assert v.is_valid(s) is False, ('should reject',s)
print('ok')
PY
