#!/usr/bin/env bash
set -u

# Gather CSS/HTML content from the workspace
{ cat /workspace/index.html /workspace/style.css /workspace/src/styles.css 2>/dev/null; } > /tmp/_typo_check.txt

# Extract font-family values, split by comma, strip quotes/whitespace, filter out generic fallbacks
custom_families=$(grep -oE 'font-family:[^;{}]+' /tmp/_typo_check.txt 2>/dev/null | \
  sed 's/font-family:[[:space:]]*//' | \
  tr ',' '\n' | \
  sed "s/['\"]//g; s/^[[:space:]]*//; s/[[:space:]]*$//" | \
  grep -vE '^\s*(sans-serif|serif|monospace|cursive|fantasy|system-ui|initial|inherit|unset)\s*$' | \
  grep -v '^$' || true)

if [ -z "$custom_families" ]; then
  echo "FAIL: No custom font families detected in the project's CSS/HTML."
  exit 1
fi

unique_count=$(echo "$custom_families" | sort -u | wc -l | tr -d '[:space:]')

if [ "$unique_count" -ge 2 ]; then
  echo "PASS: Typography deliberately pairs $unique_count distinct custom font families."
  exit 0
else
  echo "FAIL: Typography uses only $unique_count custom font family/families. Property requires at least 2."
  exit 1
fi
