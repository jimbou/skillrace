#!/usr/bin/env bash
# Lean campaign driver: exactly Random, fixed-L1 VeriGrey, and SkillRACE once per
# skill, under the same model and 30-run budget.
#
# Usage:
#   scripts/run_suite.sh                 # default skill list + reviewed protocol
#   scripts/run_suite.sh fix-failing-test build-python-cli
#
# Env knobs: PROTOCOL, WALLCLOCK, OUT. Confirmation is a separate post-campaign phase.
set -eu

PROTOCOL=${PROTOCOL:-experiments/protocols/issta-main.draft.json}
# The default intentionally fails closed until Task 8 freezes the headline protocol.
WALLCLOCK=${WALLCLOCK:-1800}
OUT=${OUT:-out/campaign}

# skill list: args, else default to the prepared in-repo skills
if [ "$#" -gt 0 ]; then SKILLS=("$@")
else SKILLS=(fix-failing-test build-python-cli mcp-server-patterns frontent-design); fi

run() {
  local method="$1" skill="$2"
  local base="skillrace/${skill}:base"
  local out="${OUT}/${method}/${skill}"
  echo "=== ${method} / ${skill}  (protocol=${PROTOCOL}) ==="
  python3 -m skillrace.loop --method "$method" \
    --skill "$skill" --skill-dir "skills/${skill}" --base "$base" \
    --props "skills/${skill}/properties.json" \
    --protocol "$PROTOCOL" \
    --wall-clock "$WALLCLOCK" \
    --out "$out"
}

for skill in "${SKILLS[@]}"; do
  run random "$skill"
  run greybox "$skill"
  run skillrace "$skill"
done

echo "=== aggregating ==="
python3 -m skillrace.aggregate --root "$OUT" --out "${OUT}/summary.json"
