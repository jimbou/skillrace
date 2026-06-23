#!/usr/bin/env bash
set -u

node -e "
const fs = require('fs');
let css = '';
try { css += fs.readFileSync('/workspace/style.css', 'utf8'); } catch(e) {}
try { css += fs.readFileSync('/workspace/index.html', 'utf8'); } catch(e) {}

// Strip comments and normalize whitespace for reliable parsing
css = css.replace(/\/\*[\s\S]*?\*\//g, '');
css = css.replace(/\s+/g, ' ');

// Check for outline suppression on :focus, *, body, or html
const suppressionRe = /(?:^|[,\s])(?:\*|:focus|body|html)[^{]*?outline\s*:\s*(none|0|0px)\b/i;
const hasSuppression = suppressionRe.test(css);

// Check for :focus-visible replacement
const replacementRe = /:focus-visible[^{]*\{[^}]*outline/i;
const hasReplacement = replacementRe.test(css);

if (hasSuppression && !hasReplacement) {
  console.log('FAIL: CSS globally suppresses focus outline without :focus-visible replacement.');
  process.exit(1);
} else {
  console.log('PASS: Focus indication is not globally suppressed.');
  process.exit(0);
}
"
exit $?
