#!/usr/bin/env bash
set -u

WORKSPACE="/workspace"
PKG="$WORKSPACE/package.json"
SRC="$WORKSPACE/src/index.ts"

# 1. Verify @modelcontextprotocol/sdk is listed in package.json
if ! grep -q '"@modelcontextprotocol/sdk"' "$PKG"; then
  echo "FAIL: @modelcontextprotocol/sdk missing from package.json dependencies"
  exit 1
fi

# 2. Verify the SDK is imported in the source code
if ! grep -q '@modelcontextprotocol/sdk' "$SRC"; then
  echo "FAIL: @modelcontextprotocol/sdk is not imported in src/index.ts"
  exit 1
fi

# 3. Verify the code actually uses the SDK API rather than hand-rolling the protocol
# Check for instantiation of the main SDK server class
if ! grep -qE '(new McpServer|import.*McpServer)' "$SRC"; then
  echo "FAIL: Source does not instantiate or import McpServer from the SDK"
  exit 1
fi

# Check for registration of tools and resources via the SDK
if ! grep -qE '\.tool\(|\.resource\(' "$SRC"; then
  echo "FAIL: Source does not register tools or resources using the SDK API"
  exit 1
fi

echo "PASS: Server correctly relies on the official MCP SDK"
exit 0
