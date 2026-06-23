#!/usr/bin/env bash
set -u

HTML_FILE="/workspace/pottery-showcase/index.html"
SRC_DIR="/workspace/pottery-showcase/src"

# 1. Verify viewport meta tag exists
if ! grep -qi '<meta[^>]*viewport' "$HTML_FILE"; then
  echo "FAIL: Missing <meta name=\"viewport\"> tag in index.html"
  exit 1
fi

# 2. Verify responsive CSS mechanisms exist in source CSS files
if ! grep -rqE '@media|clamp\(|min\(|max\(|@container' "$SRC_DIR" --include="*.css"; then
  echo "FAIL: No responsive CSS mechanisms (@media, clamp, min, max, @container) found in CSS files"
  exit 1
fi

echo "PASS: Viewport meta tag present and responsive CSS mechanisms detected."
exit 0
