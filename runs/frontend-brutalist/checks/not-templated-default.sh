#!/usr/bin/env bash
set -u

PATTERN='#F4F1EA|#F5F1E9|#EFEAE0|#C45A3B|#B5503A|#D2691E'

FOUND=0
while IFS= read -r -d '' f; do
  if grep -qiE "$PATTERN" "$f"; then
    FOUND=1
    break
  fi
done < <(find /workspace -type f \( -name "*.html" -o -name "*.css" \) -print0 2>/dev/null)

if [ "$FOUND" -eq 1 ]; then
  echo "VIOLATION: Design falls back to AI-default warm-cream-plus-terracotta palette."
  exit 1
else
  echo "PASS: Design avoids the AI-default warm-cream-plus-terracotta palette."
  exit 0
fi
