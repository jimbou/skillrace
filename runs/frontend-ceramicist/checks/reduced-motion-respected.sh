#!/usr/bin/env bash
set -u

SRC="/workspace/pottery-showcase/src"

# Check for CSS motion triggers: @keyframes, animation:, or transition:
if ! grep -rE '@keyframes|animation\s*:|transition\s*:' "$SRC" --include="*.vue" --include="*.css" >/dev/null 2>&1; then
  echo "No CSS animations or transitions detected; property holds vacuously."
  exit 0
fi

# Motion detected; verify reduced-motion support
if grep -q 'prefers-reduced-motion' "$SRC" --include="*.vue" --include="*.css" >/dev/null 2>&1; then
  echo "prefers-reduced-motion media query found; property holds."
  exit 0
else
  echo "CSS motion properties found but missing prefers-reduced-motion media query; property violated."
  exit 1
fi
