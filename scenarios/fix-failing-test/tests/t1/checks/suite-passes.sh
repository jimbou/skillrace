#!/usr/bin/env bash
set -u
cd /workspace
[ -f test_mathutil.py ] || { echo 'FAIL protected test missing or renamed'; exit 1; }
python_bin=/__SKILLRACE_TRUSTED_PYTHON__
if [ ! -x "$python_bin" ]; then
    for candidate in /usr/local/bin/python3 /usr/bin/python3; do
        if [ -x "$candidate" ]; then python_bin=$candidate; break; fi
    done
fi
[ -x "$python_bin" ] || { echo 'FAIL trusted Python unavailable'; exit 1; }
case "$python_bin" in /workspace/*) echo 'FAIL workspace-shadowed Python'; exit 1;; esac
out=$(mktemp); isolated_home=$(mktemp -d)
trap 'rm -f "$out"; rm -rf "$isolated_home"' EXIT
env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$isolated_home" LANG=C.UTF-8 \
    PYTHONNOUSERSITE=1 PYTHONSAFEPATH=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PYTEST_ADDOPTS='' PYTHONPATH='' PYTHONHOME='' \
    "$python_bin" -I -m pytest -q -c /dev/null --noconftest -p no:cacheprovider test_mathutil.py >"$out" 2>&1
rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL suite exit=$rc"; tail -20 "$out"; exit 1; }
grep -Eq '(^|[[:space:]])2 passed([[:space:]]|$)' "$out" || { echo 'FAIL expected 2 executed passing test(s)'; cat "$out"; exit 1; }
grep -Eqi 'skipped|deselected|xfailed|xpassed|no tests ran' "$out" && { echo 'FAIL suite did not execute exactly the protected tests'; cat "$out"; exit 1; }
echo ok
