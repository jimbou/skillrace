#!/usr/bin/env bash
set -u

WORKSPACE="/workspace"
FOUND_VALID_HTML=false

while IFS= read -r -d '' html_file; do
  # Check for <body> tag
  if ! grep -qi '<body' "$html_file"; then
    continue
  fi

  # Check for substantive content inside <body>
  body_content=$(sed -n '/<body[^>]*>/,/<\/body>/p' "$html_file")
  # Strip HTML tags and whitespace to check for actual text
  clean_text=$(echo "$body_content" | tr -d '[:space:]' | sed 's/<[^>]*>//g')
  if [ -z "$clean_text" ]; then
    continue
  fi

  FOUND_VALID_HTML=true
  echo "PASS: Found valid HTML file with substantive body content: $html_file"

  # Extract href and src attributes, filtering out external/protocol/data/js/mailto/root-relative paths
  refs=$(grep -oE '(href|src)="[^"]+"' "$html_file" | sed 's/.*="//;s/"$//' | grep -v '^http' | grep -v '^//' | grep -v '^data:' | grep -v '^javascript:' | grep -v '^mailto:' | grep -v '^/' || true)
  
  html_dir=$(dirname "$html_file")
  for ref in $refs; do
    [ -z "$ref" ] && continue
    
    # Check relative to HTML file directory
    if [ -f "$html_dir/$ref" ]; then
      continue
    fi
    # Check relative to workspace root (covers root-relative paths if any slip through)
    if [ -f "$WORKSPACE/$ref" ]; then
      continue
    fi
    
    echo "FAIL: Broken local reference in $html_file: $ref"
    exit 1
  done
done < <(find "$WORKSPACE" -type f -name "*.html" -print0 2>/dev/null)

if [ "$FOUND_VALID_HTML" = false ]; then
  echo "FAIL: No HTML file with substantive body content found."
  exit 1
fi

echo "PASS: All checks passed. Self-contained renderable web artifact verified."
exit 0
