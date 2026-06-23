#!/usr/bin/env bash
# run_once.sh — minimal "external Runner": run the pi agent once in the pi-base
# image on a (prompt, skill), in an isolated container, capturing the trace to a
# host log folder. This is the manual stand-in for Component 1 (the Runner).
#
# usage: run_once.sh <model> <skill_dir> <out_dir> "<prompt>"
#   e.g. run_once.sh qwen3.6-flash skills/file-check out/run1 "Create greeting.txt with 'hi' and verify"
#
# requires: docker, the skillrace/pi-base:dev image, and CLOSE_API_KEY in the env.
set -euo pipefail

MODEL="${1:?model}"; SKILL_DIR="${2:?skill dir}"; OUT="${3:?out dir}"; PROMPT="${4:?prompt}"
: "${CLOSE_API_KEY:?CLOSE_API_KEY must be set in the environment}"
WALL_CLOCK="${WALL_CLOCK:-240}"          # wall-clock timeout (D-RUN-1: no step cap)
IMAGE="${IMAGE:-skillrace/pi-base:latest}"

mkdir -p "$OUT"
SKILL_NAME="$(basename "$SKILL_DIR")"

# The key is passed at runtime via -e (never baked); --network=host gives egress.
# The prompt/model/skill go in via env to avoid shell-quoting issues.
set +e
timeout --signal=KILL "$WALL_CLOCK" \
  docker run --rm --network=host \
    --name "skillrace-run-$$" \
    -e CLOSE_API_KEY \
    -e PI_MODEL="$MODEL" -e PI_SKILL="$SKILL_NAME" -e PI_PROMPT="$PROMPT" \
    -v "$(realpath "$SKILL_DIR"):/skills/$SKILL_NAME:ro" \
    -v "$(realpath "$OUT"):/logs" \
    "$IMAGE" \
    sh -c 'pi --provider closeai --model "$PI_MODEL" --print \
              --session /logs/session.jsonl --skill "/skills/$PI_SKILL" \
              "$PI_PROMPT" </dev/null' \
    >"$OUT/stdout.txt" 2>"$OUT/stderr.txt"
RC=$?
set -e

# Write a first-class cost summary artifact (cost.json) from the trace's usage data.
if [ -f "$OUT/session.jsonl" ]; then
  python3 - "$OUT/session.jsonl" "$OUT/cost.json" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
tot = {"input": 0, "output": 0, "total_tokens": 0, "cost_usd": 0.0}
turns = []
for line in open(src):
    m = json.loads(line).get("message", {})
    if m.get("role") == "assistant":
        u = m.get("usage") or {}; c = u.get("cost") or {}
        ct = c.get("total", 0) if isinstance(c, dict) else 0
        turns.append({"model": m.get("model"), "input": u.get("input", 0),
                      "output": u.get("output", 0), "total": u.get("totalTokens", 0),
                      "cost_usd": ct})
        tot["input"] += u.get("input", 0) or 0
        tot["output"] += u.get("output", 0) or 0
        tot["total_tokens"] += u.get("totalTokens", 0) or 0
        tot["cost_usd"] += ct or 0
json.dump({"model": turns[0]["model"] if turns else None, "turns": len(turns),
           "total": tot, "by_turn": turns}, open(dst, "w"), indent=2)
print(f"cost: ${tot['cost_usd']:.6f}  ({tot['total_tokens']} tokens, {len(turns)} turns)")
PY
fi

echo "exit_code=$RC  (124/137 = timed out)"
echo "trace:  $OUT/session.jsonl"
echo "cost:   $OUT/cost.json"
echo "stdout: $OUT/stdout.txt"
[ -f "$OUT/session.jsonl" ] && echo "session lines: $(wc -l < "$OUT/session.jsonl")" || echo "NO session.jsonl"
exit "$RC"
