#!/usr/bin/env bash
# Offline quality gate for D2 scenarios: every check script must parse (bash -n),
# every test must have a candidate.json + Dockerfile + at least one check.
set -u
fail=0
for t in */tests/*/; do
  [ -f "$t/candidate.json" ] || { echo "MISSING candidate.json: $t"; fail=1; }
  [ -f "$t/Dockerfile" ]     || { echo "MISSING Dockerfile: $t"; fail=1; }
  python3 -c "import json,sys; json.load(open('$t/candidate.json'))" 2>/dev/null || { echo "BAD JSON: $t/candidate.json"; fail=1; }
  n=$(ls "$t"/checks/*.sh 2>/dev/null | wc -l)
  [ "$n" -ge 1 ] || { echo "NO checks: $t"; fail=1; }
done
for s in */tests/*/checks/*.sh; do
  bash -n "$s" || { echo "SYNTAX ERROR: $s"; fail=1; }
done
tests=$(ls -d */tests/*/ | wc -l); checks=$(ls */tests/*/checks/*.sh | wc -l)
echo "scenarios=$(ls -d */ | grep -cv X) tests=$tests checks=$checks"
[ "$fail" -eq 0 ] && echo "ALL CHECKS PASS bash -n + structure" || echo "PROBLEMS FOUND"
exit $fail
