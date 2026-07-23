# GLM 4.5 and Qwen3 Coder Flash Support Design

## Goal

Add the Yunwu model IDs `glm-4.5` and `qwen3-coder-flash` without changing the
models selected by the current headline experiment manifests.

## Observed provider contracts

Yunwu's `/v1/models` endpoint lists both exact identifiers. Live probes on 2026-07-13
showed:

- `glm-4.5` rejects synchronous requests, succeeds with `stream: true`, returns
  `reasoning_content`, and emits structured OpenAI tool calls.
- `qwen3-coder-flash` accepts synchronous and streaming requests and emits structured
  OpenAI tool calls. Neither mode returned `reasoning_content` or another dedicated
  thinking field, including when `enable_thinking: true`; reasoning-like prose appeared
  only in ordinary visible `content`.

## Capability policy

The model policy separates four concepts:

- `SUPPORTED_MODELS`: direct-call/build/catalog support;
- `AGENT_MODELS`: structured tool calls suitable for stock Pi;
- `REASONING_TRACE_MODELS`: Pi-capable models that expose a distinct reasoning trace;
- `EXPERIMENT_MODELS`: the model tracks selected by current headline protocols.

Both new models enter the supported and agent inventories. Only `glm-4.5` enters the
reasoning-trace inventory. Full SkillRACE campaign protocols require a reasoning-trace
model because the technique consumes reasoning episodes. Qwen remains usable for direct
helpers and manual/baseline Pi investigations but cannot silently become a full
SkillRACE model track.

## Streaming transport

Direct requests for `glm-4.5` include `stream: true` and
`stream_options.include_usage: true`. The client parses the complete bounded SSE
response into the same validated internal response shape used by synchronous models.
It requires one stable response ID/model, a terminal finish reason, content, and final
usage. Malformed, incomplete, inconsistent, or error events fail closed and remain
journaled under the existing request identity and outcome rules.

## Pi catalogs and accounting

Each model receives a one-model Pi catalog. Generic image and hello tools accept both.
No Yunwu credit prices have been supplied, so usage is recorded with unknown billing
rather than zero cost. Production/headline execution still requires a dated rate and
explicit experiment selection.

## Verification

Tests cover inventory separation, streaming request identity and SSE aggregation,
malformed stream rejection, catalogs, CLI/build allowlists, and campaign capability
gates. Live verification builds both Pi images and checks artifact creation, structured
tools, and exposed reasoning in their traces.

