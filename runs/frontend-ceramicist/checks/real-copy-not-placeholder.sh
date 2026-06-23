#!/usr/bin/env bash
set -u

PATTERNS='lorem ipsum|placeholder text|your text here|todo|heading 1|subtitle here'

RESULT=$(find /workspace/pottery-showcase -type f \( -name "*.vue" -o -name "*.js" -o -name "*.css" -o -name "*.html" \) ! -path "*/node_modules/*" -exec grep -liE "$PATTERNS" {} + 2>/dev/null || true)

if [ -n "$RESULT" ]; then
  echo "VIOLATION: Placeholder filler found in: $RESULT"
  exit 1
else
  echo "Property holds: No placeholder filler detected in source files."
  exit 0
fi
