#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
bad=['256.0.0.1', '1.2.3', '1.2.3.4.5', '01.2.3.4', '1.2.3.', 'a.b.c.d']
for s in bad: assert v.is_valid(s) is False, ('should reject',s)
print('ok')
PY
