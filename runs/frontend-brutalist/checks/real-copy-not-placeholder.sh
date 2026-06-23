#!/usr/bin/env bash

PLACEHOLDERS='lorem ipsum|placeholder text|your text here|TODO|Heading 1|Subtitle here'

if grep -qiE "$PLACEHOLDERS" /workspace/index.html; then
  echo "FAIL: Rendered text contains placeholder filler."
  exit 1
else
  echo "PASS: Rendered text contains no placeholder filler."
  exit 0
fi
