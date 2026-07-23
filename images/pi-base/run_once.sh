#!/usr/bin/env bash
# run_once.sh — minimal "external Runner": run the pi agent once in the pi-base
# image on a (prompt, skill), in an isolated container, capturing the trace to a
# host log folder. This is the manual stand-in for Component 1 (the Runner).
#
# usage: run_once.sh <model> <skill_dir> <out_dir> "<prompt>"
#   e.g. run_once.sh glm-4.5-flash skills/file-check out/run1 "Create greeting.txt with 'hi' and verify"
#
# requires: docker, the skillrace/pi-base:dev image, and yunwu_key in the env.
set -euo pipefail

MODEL="${1:?model}"; SKILL_DIR="${2:?skill dir}"; OUT="${3:?out dir}"; PROMPT="${4:?prompt}"
: "${yunwu_key:?yunwu_key must be set in the environment}"
WALL_CLOCK="${WALL_CLOCK:-240}"          # wall-clock timeout (D-RUN-1: no step cap)
case "$MODEL" in
  glm-4.5-flash|glm-4.5|glm-4.5-air|glm-4.7|grok-4.3|grok-4-1-fast-reasoning|qwen3.5-plus|qwen3-coder-flash|qwen3-coder-480b-a35b-instruct|deepseek-v4-flash|deepseek-v3.2) ;;
  *) echo "unsupported Yunwu model: $MODEL" >&2; exit 2 ;;
esac
IMAGE="${IMAGE:-skillrace/pi-base:0.73.1-${MODEL}}"

mkdir -p "$OUT"
SKILL_NAME="$(basename "$SKILL_DIR")"
CONTAINER_NAME="skillrace-run-$$"
cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# The key is passed at runtime via -e (never baked); --network=host gives egress.
# The prompt/model/skill go in via env to avoid shell-quoting issues.
set +e
timeout --signal=KILL "$WALL_CLOCK" \
  docker run --rm --network=host \
    --name "$CONTAINER_NAME" \
    -e yunwu_key \
    -e PI_MODEL="$MODEL" -e PI_SKILL="$SKILL_NAME" -e PI_PROMPT="$PROMPT" \
    -v "$(realpath "$SKILL_DIR"):/skills/$SKILL_NAME:ro" \
    -v "$(realpath "$OUT"):/logs" \
    "$IMAGE" \
    sh -c 'pi --provider yunwu --model "$PI_MODEL" --print \
              --session /logs/session.jsonl --skill "/skills/$PI_SKILL" \
              "$PI_PROMPT" </dev/null' \
    >"$OUT/stdout.txt" 2>"$OUT/stderr.txt"
RC=$?
set -e
cleanup

# Write a first-class cost summary artifact (cost.json) from the trace's usage data.
if [ -f "$OUT/session.jsonl" ]; then
  python3 - "$OUT/session.jsonl" "$OUT/cost.json" "$MODEL" <<'PY'
import json, sys
src, dst, requested_model = sys.argv[1:]
rates = {
    "glm-4.5-flash": (0.02, 0.08, 0.02),
    "deepseek-v4-flash": (1.0, 2.0, 0.02),
    "deepseek-v3.2": (2.0, 3.0, 2.0),
}
tot = {"input": 0, "output": 0, "cache_read": 0, "total_tokens": 0}
turns = []
for line in open(src):
    m = json.loads(line).get("message", {})
    if m.get("role") == "assistant":
        u = m.get("usage") or {}
        cache_read = u.get("cacheRead", 0) or 0
        turns.append({"model": m.get("model"), "input": u.get("input", 0),
                      "output": u.get("output", 0), "total": u.get("totalTokens", 0),
                      "cache_read": cache_read})
        tot["input"] += u.get("input", 0) or 0
        tot["output"] += u.get("output", 0) or 0
        tot["cache_read"] += cache_read
        tot["total_tokens"] += u.get("totalTokens", 0) or 0
rate = rates.get(requested_model)
provider_credits = None
if rate is not None:
    input_rate, output_rate, cache_rate = rate
    provider_credits = (
        tot["input"] * input_rate
        + tot["output"] * output_rate
        + tot["cache_read"] * cache_rate
    ) / 1_000_000
result = {
    "model": turns[0]["model"] if turns else requested_model,
    "requested_model": requested_model,
    "turns": len(turns),
    "in": tot["input"],
    "out": tot["output"],
    "cache_read": tot["cache_read"],
    "total_tokens": tot["total_tokens"],
    "billing_status": "known" if provider_credits is not None else "unknown",
    "billing_currency": "YUNWU_CREDIT" if provider_credits is not None else None,
    "cost_provider_credits": provider_credits,
    "price_provider_credits": round(provider_credits, 6) if provider_credits is not None else None,
    "cost_usd": None,
    "by_turn": turns,
}
with open(dst, "w", encoding="utf-8") as stream:
    json.dump(result, stream, indent=2)
cost = f"⚡{provider_credits:.8f}" if provider_credits is not None else "unknown"
print(f"cost: {cost}  ({tot['total_tokens']} tokens, {len(turns)} turns)")
PY
fi

echo "exit_code=$RC  (124/137 = timed out)"
echo "trace:  $OUT/session.jsonl"
echo "cost:   $OUT/cost.json"
echo "stdout: $OUT/stdout.txt"
[ -f "$OUT/session.jsonl" ] && echo "session lines: $(wc -l < "$OUT/session.jsonl")" || echo "NO session.jsonl"
exit "$RC"
