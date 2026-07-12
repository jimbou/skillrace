#!/usr/bin/env bash
# Location-independent offline gate. Invoke with `bash scenarios/lint_checks.sh`;
# executable mode is not required by the artifact protocol.
set -u

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(dirname -- "$script_dir")
python=${SKILLRACE_PYTHON:-python3}

cd "$repo_root" || exit 1
"$python" -m skillrace.scenario_contract validate "$script_dir"
