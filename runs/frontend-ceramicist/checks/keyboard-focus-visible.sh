#!/usr/bin/env bash
set -u

SRC_DIR="/workspace/pottery-showcase/src"
VIOLATED=0

while IFS= read -r -d '' f; do
    if grep -qiE 'outline[[:space:]]*:[[:space:]]*(none|0)' "$f"; then
        if ! grep -q ':focus-visible' "$f"; then
            VIOLATED=1
            echo "Violation: $f contains outline suppression without :focus-visible replacement"
        fi
    fi
done < <(find "$SRC_DIR" -type f \( -name "*.css" -o -name "*.vue" \) -print0)

if [ "$VIOLATED" -eq 1 ]; then
    echo "FAIL: Focus indication is suppressed without adequate replacement."
    exit 1
else
    echo "PASS: Focus indication is preserved or properly replaced."
    exit 0
fi
