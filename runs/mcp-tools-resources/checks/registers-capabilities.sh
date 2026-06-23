#!/usr/bin/env bash
set -u

FILE="/workspace/src/index.ts"

if [ ! -f "$FILE" ]; then
    echo "FAIL: src/index.ts not found."
    exit 1
fi

if ! grep -qE 'new[[:space:]]+(McpServer|Server)[[:space:]]*\(' "$FILE"; then
    echo "FAIL: No MCP server instantiation found."
    exit 1
fi

if ! grep -qE '\.(tool|registerTool|resource|registerResource|prompt|registerPrompt)[[:space:]]*\(' "$FILE"; then
    echo "FAIL: No tool/resource/prompt registration found."
    exit 1
fi

echo "PASS: Server instantiated and at least one item registered."
exit 0
