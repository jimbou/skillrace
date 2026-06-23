#!/usr/bin/env bash
set -u

FILE="/workspace/src/index.ts"
if [ ! -f "$FILE" ]; then
  echo "FAIL: src/index.ts not found"
  exit 1
fi

node << 'NODECHECK'
const fs = require('fs');
const code = fs.readFileSync('/workspace/src/index.ts', 'utf8');

function splitArgs(str) {
  const args = [];
  let current = '';
  let depth = 0;
  for (let i = 0; i < str.length; i++) {
    const c = str[i];
    if (c === '(' || c === '{' || c === '[') depth++;
    else if (c === ')' || c === '}' || c === ']') depth--;
    if (c === ',' && depth === 0) {
      args.push(current.trim());
      current = '';
    } else {
      current += c;
    }
  }
  if (current.trim()) args.push(current.trim());
  return args;
}

let foundTools = 0;
let violations = [];

const toolRegex = /\.tool\s*\(/g;
let match;
while ((match = toolRegex.exec(code)) !== null) {
  foundTools++;
  const openParenIdx = code.indexOf('(', match.index);
  let depth = 0;
  let endIdx = openParenIdx;
  for (let i = openParenIdx; i < code.length; i++) {
    if (code[i] === '(') depth++;
    else if (code[i] === ')') {
      depth--;
      if (depth === 0) { endIdx = i; break; }
    }
  }
  
  const argsStr = code.slice(openParenIdx + 1, endIdx);
  const args = splitArgs(argsStr);
  
  if (args.length < 3) {
    violations.push('Missing schema argument');
    continue;
  }
  
  const schemaArg = args[2];
  if (schemaArg === '{}' || schemaArg === 'null' || schemaArg === 'undefined') {
    violations.push('Empty or untyped schema');
    continue;
  }
  
  if (!schemaArg.includes('z.') && !schemaArg.includes('inputSchema') && !schemaArg.startsWith('{')) {
    violations.push('Schema does not use Zod or inputSchema');
    continue;
  }
}

if (violations.length > 0) {
  console.log('FAIL: Schema-first property violated: ' + violations.join('; '));
  process.exit(1);
}

if (foundTools === 0) {
  console.log('PASS: No tools registered (vacuously holds)');
} else {
  console.log('PASS: All ' + foundTools + ' registered tool(s) declare valid input schemas.');
}
process.exit(0);
NODECHECK

exit $?
