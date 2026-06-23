#!/usr/bin/env bash
set -u

cd /workspace || exit 1

# Install dependencies
npm install > /dev/null 2>&1 || { echo "FAIL: npm install failed"; exit 1; }

# Compile TypeScript
COMPILE_OK=false
if npx tsc --noEmit 2>/dev/null; then
    COMPILE_OK=true
elif npm run build > /dev/null 2>&1; then
    COMPILE_OK=true
fi

if [ "$COMPILE_OK" = false ]; then
    echo "FAIL: TypeScript compilation failed"
    exit 1
fi

# Determine expected output file
EXPECTED_OUTPUT=""
PKG_MAIN=$(node -e "console.log(require('./package.json').main || '')")
PKG_BIN=$(node -e "const p=require('./package.json'); console.log(typeof p.bin==='string'?p.bin:(typeof p.bin==='object'?Object.values(p.bin)[0]||'':''))")

if [ -n "$PKG_MAIN" ]; then
    EXPECTED_OUTPUT="$PKG_MAIN"
elif [ -n "$PKG_BIN" ]; then
    EXPECTED_OUTPUT="$PKG_BIN"
else
    OUTDIR=$(node -e "try { console.log(require('./tsconfig.json').compilerOptions?.outDir || '') } catch(e) { console.log('') }")
    if [ -n "$OUTDIR" ] && [ -d "$OUTDIR" ]; then
        BASENAME=$(node -e "try { console.log(require('./package.json').name.split('/').pop() || 'index') } catch(e) { console.log('index') }")
        if [ -f "$OUTDIR/${BASENAME}.js" ]; then
            EXPECTED_OUTPUT="$OUTDIR/${BASENAME}.js"
        elif [ -f "$OUTDIR/index.js" ]; then
            EXPECTED_OUTPUT="$OUTDIR/index.js"
        fi
    fi
fi

if [ -z "$EXPECTED_OUTPUT" ]; then
    echo "FAIL: Could not determine declared entry point"
    exit 1
fi

# Resolve absolute path
if [[ "$EXPECTED_OUTPUT" != /* ]]; then
    ABS_PATH="/workspace/$EXPECTED_OUTPUT"
else
    ABS_PATH="$EXPECTED_OUTPUT"
fi

if [ ! -f "$ABS_PATH" ]; then
    echo "FAIL: Declared entry point $ABS_PATH does not exist after build"
    exit 1
fi

echo "PASS: Project compiles without type errors and declared entry point exists"
exit 0
