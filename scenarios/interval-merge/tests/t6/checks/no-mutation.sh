#!/usr/bin/env bash
# skillrace-oracle-v1
set -u
cd /workspace
[ -f intervals.py ] || { echo 'FAIL intervals.py missing'; exit 1; }
python3 - <<'PYORACLE'
from intervals import merge
items = [[5, 7], [1, 3], [2, 4]]
before = [item[:] for item in items]
result = merge(items)
assert items == before, (items, before)
assert result == [[1, 4], [5, 7]], result
assert result is not items
print('ok')
PYORACLE
