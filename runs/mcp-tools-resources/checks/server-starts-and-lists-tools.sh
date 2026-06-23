#!/usr/bin/env bash
set -u

cd /workspace || exit 1

SERVER_STDERR=$(mktemp)
SERVER_OUT=$(mktemp)

# Launch server as stdio process, send MCP handshake and tools/list
{
  echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0.0"}}}'
  sleep 2
  echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
  sleep 2
} | node --import tsx src/index.ts > "$SERVER_OUT" 2> "$SERVER_STDERR"
SERVER_EXIT=$?

if [ $SERVER_EXIT -ne 0 ]; then
  echo "FAIL: Server exited with code $SERVER_EXIT."
  cat "$SERVER_STDERR" >&2
  rm -f "$SERVER_STDERR" "$SERVER_OUT"
  exit 1
fi

if grep -qiE "(TypeError|ReferenceError|SyntaxError|RangeError|Stack trace|at .*\.(js|ts):)" "$SERVER_STDERR"; then
  echo "FAIL: Server emitted error or stack trace."
  cat "$SERVER_STDERR" >&2
  rm -f "$SERVER_STDERR" "$SERVER_OUT"
  exit 1
fi

node -e "
const fs = require('fs');
const out = fs.readFileSync('$SERVER_OUT', 'utf8').trim();
const lines = out.split('\n').filter(l => l.trim());
let initOk = false;
let toolsOk = false;
let hasTool = false;

for (const line of lines) {
  try {
    const msg = JSON.parse(line);
    if (msg.id === 1 && msg.result && msg.result.capabilities) initOk = true;
    if (msg.id === 2 && msg.result && Array.isArray(msg.result.tools)) {
      toolsOk = true;
      if (msg.result.tools.length > 0) hasTool = true;
    }
  } catch(e) {}
}

if (!initOk) { console.log('FAIL: Missing valid initialize response'); process.exit(1); }
if (!toolsOk) { console.log('FAIL: Missing valid tools/list response'); process.exit(1); }
if (!hasTool) { console.log('FAIL: tools/list returned empty array'); process.exit(1); }
console.log('PASS: MCP server started, initialized, and listed tools successfully.');
process.exit(0);
"
RES=$?
rm -f "$SERVER_STDERR" "$SERVER_OUT"
exit $RES
