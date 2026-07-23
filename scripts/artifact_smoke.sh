#!/usr/bin/env bash
# Offline, no-cost artifact gate. This intentionally never contacts Yunwu and never
# starts an agent-under-test run. It verifies the core experimental contracts and the
# checked-in D1/D2 evidence on a clean machine.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

PYTHON=${PYTHON:-python3}

"$PYTHON" -m compileall -q skillrace tests

"$PYTHON" -m pytest -q -m "not live" \
  tests/test_artifact_smoke.py \
  tests/test_campaign_protocol.py \
  tests/test_baseline_information_boundaries.py \
  tests/test_campaign_engine.py \
  tests/test_campaign_parallel_engine.py \
  tests/test_experiment_driver.py \
  tests/test_analyze_rq1.py \
  tests/test_analyze_rq3.py \
  tests/test_epoch_planning.py \
  tests/test_segment_fold_artifacts.py \
  tests/test_candidate_sanity.py \
  tests/test_closeai_journal.py \
  tests/test_provider_evidence.py \
  tests/test_yunwu_model_freeze.py \
  tests/test_schedules.py \
  tests/test_development_pilot_schedule.py \
  tests/test_artifact_freeze.py \
  tests/test_rq3_driver.py \
  tests/test_rq3_prepare.py \
  tests/test_compile_identity.py \
  tests/test_check_isolation.py \
  tests/test_d1_suite.py \
  tests/test_d1_images.py \
  tests/test_property_specs.py \
  tests/test_rq3_leakage.py \
  tests/test_rq3_manifest.py \
  tests/test_rq3_pipeline.py \
  tests/test_scenario_contract.py \
  tests/test_scenario_offline.py

"$PYTHON" -m skillrace.d1_audit \
  experiments/manifests/rq1-skills.draft.json

"$PYTHON" -m skillrace.provider_evidence \
  experiments/provider-evidence/yunwu-2026-07-12/rate-card.json \
  --runtime-probes \
  experiments/provider-evidence/yunwu-2026-07-12/runtime-probes.json

if [ "${SKILLRACE_SMOKE_REQUIRE_IMAGES:-0}" = "1" ]; then
  "$PYTHON" -m skillrace.d1_audit \
    experiments/manifests/rq1-skills.draft.json --require-images
  "$PYTHON" -m skillrace.d1_images \
    --out experiments/image-locks --validate --require-images
fi

"$PYTHON" -m skillrace.scenario_contract validate scenarios \
  --require-runtime-evidence

echo "SkillRACE offline artifact smoke: PASS"
