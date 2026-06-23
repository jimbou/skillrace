#!/usr/bin/env bash
set -u

WORKSPACE="/workspace"
HTML_FILE="$WORKSPACE/index.html"
EXIT_CODE=0
REASON=""

# 1. Verify HTML file exists
if [[ ! -f "$HTML_FILE" ]]; then
  REASON="FAIL: index.html not found in $WORKSPACE"
  echo "$REASON"
  exit 1
fi

# 2. Extract local/relative href/src paths from index.html
# Matches href="..." or src="..." where value starts with . or / but NOT http(s)://
LOCAL_PATHS=$(grep -oP '(?:href|src)\s*=\s*"(?!(?:https?://))([^"]+)"' /workspace/index.html 2>/dev/null || true)

if [[ -z "$LOCAL_PATHS" ]]; then
  # No local/relative paths found is acceptable (e.g., only external URLs used)
  echo "PASS: No local/relative resource paths to validate."
  exit 0
fi

# Validate each local/relative path exists under /workspace
VIOLATION_FOUND=0
while IFS= read -r rel_path; do
  # Resolve relative path against /workspace
  resolved_path="/workspace/$rel_path"
  if [[ ! -f "$resolved_path" ]]; then
    echo "FAIL: Local/relative resource path '$resolved_path' does not exist."
    exit 1
  fi
done

echo "PASS: All local/relative resource paths validated successfully."
exit 0
