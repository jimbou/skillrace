#!/usr/bin/env bash
set -u
cd /workspace
if [ -f /check/workspace.diff ]; then grep -qE '^\+\+\+ b/test_core.py' /check/workspace.diff && { echo 'FAIL edited tests'; exit 1; }; fi; echo ok
