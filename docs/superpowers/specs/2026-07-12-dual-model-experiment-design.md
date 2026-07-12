# Dual-Model Experiment Design

## Decision

Run the complete SkillRACE evaluation twice as two independent, model-frozen
tracks:

1. **GLM track:** `glm-4.5-flash` is used for the agent under test and every
   model-driven role.
2. **DeepSeek track:** `deepseek-v4-flash` is used for the agent under test and
   every model-driven role.

Within a track, the selected model is identical for generation, realization,
build repair, the agent run, segmentation, tree construction, checking,
confirmation, feedback, revision, and hidden evaluation. No role may use the
other track's model.

## Reporting

Each track has its own model identifier, dated Yunwu rate-card snapshot, image
digest, schedules, model-call journals, cost ledger, and result tables. Report
all primary outcomes separately by model. A cross-model comparison is a
robustness analysis; it must not pool runs, costs, or defect counts into one
headline estimate.

## Model-selection evidence (2026-07-12)

Both selected Yunwu model IDs returned a `reasoning_content` field in a minimal
live probe, so their reasoning can be captured for SkillRACE's guard signal.

| Model | Probe latency | Trace evidence | Cost evidence |
| --- | ---: | --- | --- |
| `glm-4.5-flash` | 6.1 s | `reasoning_content`, 258 reasoning tokens | Exact Yunwu rate for this model ID is not yet archived. A third-party Yunwu listing for the related `glm-4.5` model reports sale and standard tiers of $0.066/$0.263 and $0.110/$0.438 per million input/output tokens, respectively; this is not a billable experiment rate. |
| `deepseek-v4-flash` | 12.5 s | `reasoning_content`, 46 reasoning tokens | A third-party Yunwu listing reports $0.068/$0.137 per million input/output tokens. This is planning evidence only until the Yunwu rate card is archived. |

The probe latencies are one-request observations, not throughput or quality
benchmarks. Reasoning tokens count toward output-token billing.

## Preconditions before a headline run

For each track:

1. Archive the dated Yunwu model-group rate card, including input, output,
   cache, currency, retrieval time, source, and a content hash.
2. Configure the exact model ID in Pi and direct-call accounting, rebuild the
   shared Pi image, and record its immutable digest.
3. Run one direct request and one Pi agent request to verify the model ID,
   billing receipt, and a Pi `thinking` block.
4. Generate and hash separate complete schedules before either track begins.

## Non-goals

This decision does not claim either model is more capable overall, does not set
a single blended price, and does not authorize pooling the two tracks' results.
