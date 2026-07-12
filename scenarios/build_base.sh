#!/usr/bin/env bash
# Compatibility entry point for the D2 documentation. The authoritative,
# lock-checked source is shared with the RQ1 environments.
set -euo pipefail
cd "$(dirname "$0")/.."
exec images/skillgen-base/build.sh
