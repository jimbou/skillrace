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

## Model-catalog amendment rule (2026-07-13)

`deepseek-v3.2` has passed limited development compatibility probes and is eligible for
consideration alongside the current two tracks. It is **not** in this draft's selected
catalog yet. Before any paid headline execution, the final model inventory will be
hardcoded and frozen. If V3.2 is included, it receives its own complete independent
track—dated rate evidence, Pi/D1 image locks, direct and Pi preflights, protocol,
schedule, journals, output root, and analysis table. No model is added after the first
headline run, and no observations are pooled across models.

## Model-selection evidence (2026-07-12)

Both selected Yunwu model IDs returned nonzero reasoning-token usage in a direct
probe and nonempty Pi `thinking` blocks in a multi-turn tool-use probe. These are
development-only connectivity artifacts, not pilot or headline observations.

| Model | Direct evidence | Pi 0.73.1 evidence | Frozen default-group rate |
| --- | --- | --- | --- |
| `glm-4.5-flash` | HTTP 200; exact model; 60 reasoning tokens | 3 turns; 2 thinking turns; write, read, final response | ⚡0.02/M input; ⚡0.08/M output |
| `deepseek-v4-flash` | HTTP 200; exact model; 32 reasoning tokens | 3 turns; 3 thinking turns; tool use and final response | ⚡1.00/M input; ⚡2.00/M output; ⚡0.02/M cache read |

The rate-card extract and source hashes are archived under
`experiments/provider-evidence/yunwu-2026-07-12/`. Yunwu labels the unit as a custom
`⚡` currency; it is not reported as USD. GLM has no advertised cache ratio, so cached
input is conservatively charged at its ordinary input rate. The same directory contains
the redacted direct journal, successful Pi traces, exact image/config hashes, failed
compatibility diagnostics, and an offline validator. Reasoning tokens count toward
output-token billing.

Pi 0.62 exposed a provider-compatibility defect for GLM tool-result history: null
assistant content first caused HTTP 500, and a partial compatibility setting then caused
repeated tool calls. Pi 0.73.1 plus the model-specific `requiresAssistantAfterToolResult`
and `requiresThinkingAsText` settings completed the same write/read task normally. The
fix is transport-level and applies to every skill and method; no evaluation prompt was
tuned on a skill.

## Preconditions before a headline run

For each track:

1. **Complete:** archive the dated Yunwu model-group rate card, including input, output,
   cache, currency, retrieval time, source, and a content hash.
2. **Complete:** configure the exact model ID in Pi and direct-call accounting, rebuild the
   shared Pi image, and record its immutable digest.
3. **Complete:** run one direct request and one Pi agent request to verify the model ID,
   billing receipt, and a Pi `thinking` block.
4. **Complete (draft):** generate and hash separate complete schedules. They become
   executable headline schedules only after the image/runtime pilot and recursive freeze
   gates succeed.

## Non-goals

This decision does not claim either model is more capable overall, does not set
a single blended price, and does not authorize pooling the two tracks' results.
