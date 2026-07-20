#!/usr/bin/env bash
# skillrace-oracle-v1
# content-addressed-integrity-v1: independent of mutable git HEAD/index state
set -u
cd /workspace
python3 - <<'PYORACLE'
import hashlib
import pathlib

root = pathlib.Path('/workspace')
protected = {'test_cmp.py': '093872e056a0126fd8f0719147fe9603684915d85ba425cf105c393e5a5e24df'}
expected_tests = set(protected)
errors = []
for relative, expected_hash in protected.items():
    path = root / relative
    if not path.is_file() or path.is_symlink():
        errors.append(f'missing, renamed, or symlinked: {relative}')
    elif hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        errors.append(f'content changed: {relative}')
actual_tests = {
    path.relative_to(root).as_posix()
    for pattern in ('test*.py', '*_test.py')
    for path in root.rglob(pattern)
    if '.git' not in path.parts and '__pycache__' not in path.parts
}
if actual_tests != expected_tests:
    errors.append(f'test file set changed: expected={sorted(expected_tests)} actual={sorted(actual_tests)}')
for path in root.rglob('*'):
    relative = path.relative_to(root)
    if path.is_dir() and path.name in {'pytest', '_pytest'}:
        errors.append(f'pytest package shadow added: {relative}')
    if path.is_file() and (
        path.name in {'conftest.py', 'pytest.ini', 'pyproject.toml', 'setup.cfg', 'tox.ini',
                      'sitecustomize.py', 'usercustomize.py', 'pytest.py',
                      'python', 'python3'}
    ):
        errors.append(f'pytest/Python collection, config, or module override added: {relative}')
if errors:
    print('FAIL ' + ' | '.join(errors))
    raise SystemExit(1)
print('ok: protected test content and harness are unchanged (VCS HEAD cannot bypass hashes)')
PYORACLE
