#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
ok=['0.0.0.0', '192.168.1.1', '255.255.255.255', '8.8.8.8']
for s in ok: assert v.is_valid(s) is True, ('should accept',s)
print('ok')
PY
