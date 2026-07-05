#!/usr/bin/env bash
# Campaign driver: run all three methods across a skill list, with the greybox
# granularity sweep, into out/campaign/<method>/<skill>/. Prep/orchestration — the
# actual agent runs are what cost time/money; this just sequences them reproducibly.
#
# Usage:
#   scripts/run_suite.sh                 # default skill list + budget
#   BUDGET=120 SEED=20 scripts/run_suite.sh fix-failing-test build-python-cli
#
# Env knobs: BUDGET, SEED, MODEL, AGENT_MODEL, WALLCLOCK, REGRADE_K, OUT.
set -u

BUDGET=${BUDGET:-120}
SEED=${SEED:-20}
MODEL=${MODEL:-qwen3.6-flash}
AGENT_MODEL=${AGENT_MODEL:-qwen3.6-flash}
WALLCLOCK=${WALLCLOCK:-1800}
REGRADE_K=${REGRADE_K:-0}
OUT=${OUT:-out/campaign}

# skill list: args, else default to the prepared in-repo skills
if [ "$#" -gt 0 ]; then SKILLS=("$@")
else SKILLS=(fix-failing-test build-python-cli mcp-server-patterns frontent-design); fi

run() { # method skill extra_out_suffix extra_flags...
  local method="$1" skill="$2" suffix="$3"; shift 3
  local base="skillrace/${skill}:base"
  local out="${OUT}/${method}${suffix}/${skill}"
  echo "=== ${method}${suffix} / ${skill}  (budget=${BUDGET}) ==="
  python3 -m skillrace.loop --method "$method" \
    --skill "$skill" --skill-dir "skills/${skill}" --base "$base" \
    --props "skills/${skill}/properties.json" \
    --budget "$BUDGET" --seed-count "$SEED" \
    --model "$MODEL" --agent-model "$AGENT_MODEL" \
    --wall-clock "$WALLCLOCK" --regrade-k "$REGRADE_K" \
    --out "$out" "$@"
}

for skill in "${SKILLS[@]}"; do
  run random   "$skill" ""
  # greybox granularity sweep: report all three, headline uses the best
  for lvl in L0 L1 L2; do
    run greybox "$skill" "-${lvl}" --greybox-level "$lvl"
  done
  run skillrace "$skill" ""
done

echo "=== aggregating ==="
python3 -m skillrace.aggregate --root "$OUT" --out "${OUT}/summary.json"
