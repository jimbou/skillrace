#!/usr/bin/env bash
# Run one complete frozen model track. Invoke a second time for the other model;
# never point both invocations at the same output root.
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${1:?usage: scripts/run_model_track.sh <glm-4.5-flash|deepseek-v4-flash> [rq1|rq3|all]}"
PHASE="${2:-all}"
case "$MODEL" in
  glm-4.5-flash|deepseek-v4-flash) ;;
  *) echo "unsupported experiment model: $MODEL" >&2; exit 2 ;;
esac
case "$PHASE" in rq1|rq3|all) ;; *) echo "phase must be rq1, rq3, or all" >&2; exit 2 ;; esac

RQ1_SCHEDULE="experiments/schedules/rq1.${MODEL}.draft.json"
RQ3_SCHEDULE="experiments/schedules/rq3.${MODEL}.draft.json"
ROOT="results/${MODEL}"

if [[ "$PHASE" == rq1 || "$PHASE" == all ]]; then
  python3 -m skillrace.experiment_driver \
    --manifest "$RQ1_SCHEDULE" --out "$ROOT/rq1" --epoch-size 1
fi
if [[ "$PHASE" == rq3 || "$PHASE" == all ]]; then
  python3 -m skillrace.rq3_driver \
    --schedule "$RQ3_SCHEDULE" --out "$ROOT/rq3"
fi
