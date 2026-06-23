#!/usr/bin/env bash
set -u

FILE="/workspace/src/index.ts"

if [ ! -f "$FILE" ]; then
    echo "VIOLATION: src/index.ts not found."
    exit 1
fi

# Check for transport instantiation or import
if ! grep -qE '(new\s+[A-Za-z_]+Transport|import\s+.*Transport)' "$FILE"; then
    echo "VIOLATION: No transport construction or import found."
    exit 1
fi

# Check for server.connect() call
if ! grep -qE '\.connect\(' "$FILE"; then
    echo "VIOLATION: No server.connect() call found."
    exit 1
fi

echo "HOLD: Server constructs a transport and calls connect()."
exit 0
