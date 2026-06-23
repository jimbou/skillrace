#!/usr/bin/env bash
set -u

# Check for motion properties in CSS/HTML files
if ! grep -rliE --include="*.css" --include="*.html" '@keyframes|animation[[:space:]]*:|transition[[:space:]]*:' /workspace >/dev/null 2>&1; then
  echo "VACUOUSLY HOLDING: No CSS animations or transitions detected."
  exit 0
fi

# Check for reduced motion preference
if grep -rliE --include="*.css" --include="*.html" '@media[[:space:]]*\(.*prefers-reduced-motion[[:space:]]*:[[:space:]]*reduce' /workspace >/dev/null 2>&1; then
  echo "HOLDS: @media (prefers-reduced-motion: reduce) block found."
  exit 0
else
  echo "VIOLATED: Motion detected but missing @media (prefers-reduced-motion: reduce) block."
  exit 1
fi
