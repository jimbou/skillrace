#!/usr/bin/env bash
set -u
cd /workspace
python3 - <<'PY'
import validate as v
v0='2024-01-01'
# surround with chars invalid in EVERY spec (space, '!') so a correctly
# anchored validator rejects them; catches solutions that use re.search / no ^$
for s in (' '+v0, v0+' ', '!'+v0+'!'):
    assert v.is_valid(s) is False, ('anchoring: should reject',s)
print('ok')
PY
