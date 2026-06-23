#!/usr/bin/env bash
set -u

HTML="/workspace/index.html"
CSS="/workspace/style.css"

# 1. Verify viewport meta tag exists in the HTML
if ! grep -qi '<meta[^>]*name="viewport"' "$HTML" 2>/dev/null; then
  echo "FAIL: Missing <meta name=\"viewport\"> tag in index.html"
  exit 1
fi

# 2. Verify at least one genuine responsive CSS mechanism exists
# Checks both inline styles in HTML and the external CSS file
FOUND_RESPONSIVE=0
for F in "$HTML" "$CSS"; do
  if [ -f "$F" ]; then
    if grep -qE '@media|clamp\(|min\(|max\(|container' "$F"; then
      FOUND_RESPONSIVE=1
      break
    fi
  fi
done

if [ "$FOUND_RESPONSIVE" -eq 0 ]; then
  echo "FAIL: No responsive CSS mechanisms (@media, clamp, min, max, container) detected"
  exit 1
fi

echo "PASS: Responsive quality floor satisfied (viewport present + responsive CSS mechanisms found)"
exit 0
