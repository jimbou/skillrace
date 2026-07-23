# Supported Yunwu Models Design

## Goal

Allow SkillRACE development tooling and Pi agent runs to use `glm-4.5-air` and
`glm-4.7` without declaring either model part of the current headline experiment.
Final experiment model selection remains a separate protocol decision.

## Design

`skillrace.model_policy` owns three distinct inventories:

- `SUPPORTED_MODELS`: every Yunwu model for which SkillRACE ships a Pi catalog and
  permits development calls;
- `AGENT_MODELS`: supported models observed to emit structured tool calls through
  Yunwu and therefore safe to use with stock Pi;
- `EXPERIMENT_MODELS`: the currently selected, rate-card-frozen headline tracks.

The supported inventory contains `glm-4.5-flash`, `glm-4.5-air`, `glm-4.7`,
`deepseek-v4-flash`, and `deepseek-v3.2`. Adding runtime support does not alter
existing protocols, schedules, image locks, or paper claims.

Generic connectivity scripts and Pi image helpers accept `SUPPORTED_MODELS`.
`glm-4.5-air` currently remains outside `AGENT_MODELS`: repeated raw Yunwu probes
returned textual pseudo-calls and no OpenAI `tool_calls`, so Pi could not execute the
requested artifact operation. `glm-4.7` passed a Pi reasoning/tool-use probe. This
capability boundary prevents silent artifact-free campaign executions while keeping Air
available for direct helper calls and future reprobes.
Experiment freeze, schedule, and headline validation continue to accept only the
models selected by `EXPERIMENT_MODELS`. Runtime development protocols may use any
supported model.

Each new GLM model receives a one-model Pi catalog using the existing Yunwu
OpenAI-compatible endpoint and reasoning-capable GLM compatibility settings. Direct
helper calls send GLM's native `thinking.type=disabled` whenever reasoning is disabled.

Provider usage is always recorded. Because no Yunwu credit rates have been supplied
for the two new models, development receipts mark billing as unknown. Production and
headline execution remain fail-closed until a dated rate is recorded and the model is
explicitly selected for that experiment.

## Verification

Tests cover inventory separation, one-model catalogs, CLI/build acceptance, GLM
thinking parameters, unknown-cost handling, and rejection of unsupported identifiers.
After local tests, the minimal Yunwu connectivity script is run once for each new model.
A bounded Pi probe is attempted for models whose direct call succeeds.
