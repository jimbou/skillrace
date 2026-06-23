#!/usr/bin/env bash
set -u

DIR="/workspace/pottery-showcase"
FILES="$DIR/src/index.css $DIR/src/App.vue $DIR/src/components/ProductCard.vue $DIR/index.html"

# Extract font-family / fontFamily values from CSS/Vue/HTML
families_raw=$(cat $FILES 2>/dev/null | grep -ioE '(font-family|fontFamily)\s*:\s*[^;]+' | \
  sed 's/.*:\s*//' | \
  tr ',' '\n' | \
  sed "s/['\"]//g" | \
  sed 's/^\s*//;s/\s*$//' | \
  sort -u)

count=0
list=""
while IFS= read -r fam; do
    [ -z "$fam" ] && continue
    # Skip generic/fallback families
    if [[ "$fam" =~ ^(sans-serif|serif|monospace|cursive|fantasy|system-ui|-apple-system|BlinkMacSystemFont|Segoe\ UI|Roboto|Helvetica\ Neue|Helvetica|Arial|Noto\ Sans|Liberation\ Sans|ui-sans-serif|ui-system|ui-monospace|emoji|math|fangsong|inherit|initial|unset|none|auto|currentcolor)$ ]]; then
        continue
    fi
    count=$((count + 1))
    list="$list $fam"
done <<< "$families_raw"

if [ "$count" -ge 2 ]; then
    echo "PASS: Typography deliberately pairs $count distinct custom font families:$list"
    exit 0
else
    echo "FAIL: Typography does not reference at least two distinct custom font families. Found:$list"
    exit 1
fi
