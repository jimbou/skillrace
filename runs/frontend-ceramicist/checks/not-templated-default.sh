#!/usr/bin/env bash
set -u

WORKSPACE="/workspace/pottery-showcase"

# Patterns for the AI-default cliché palette
BG_PATTERN='#F4F1EA|#F5F1E9|#EFEAE0'
ACCENT_PATTERN='#C45A3B|#B5503A|#D2691E'

# Check for background colors in source files
HAS_BG=$(grep -rqEi "($BG_PATTERN)" "$WORKSPACE" --include="*.vue" --include="*.css" --include="*.js" --exclude-dir="node_modules" 2>/dev/null && echo 1 || echo 0)

# Check for accent colors in source files
HAS_ACC=$(grep -rqEi "($ACCENT_PATTERN)" "$WORKSPACE" --include="*.vue" --include="*.css" --include="*.js" --exclude-dir="node_modules" 2>/dev/null && echo 1 || echo 0)

if [ "$HAS_BG" = "1" ] && [ "$HAS_ACC" = "1" ]; then
    echo "VIOLATION: The design falls back to the AI-default warm-cream-plus-terracotta palette."
    exit 1
else
    echo "HOLD: The design avoids the AI-default warm-cream-plus-terracotta palette."
    exit 0
fi
