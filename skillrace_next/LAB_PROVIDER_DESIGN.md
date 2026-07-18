# Lab Provider Integration

## Scope

Add the lab OpenAI-compatible gateway alongside Yunwu without creating a second pipeline.
The selected `(provider, model_id)` pair controls both direct calls and Pi agents. Codex
remains the verifier and is unchanged.

Supported pairs are:

- `yunwu/deepseek-v3.2`
- `lab/deepseek-v4-flash`, routed upstream as `ds/deepseek-v4-flash`
- `lab/qwen3.6-flash`, routed upstream as `ali/qwen3.6-flash`

The experiment config continues to store `provider` and `model_id` separately. Receipts
record the provider-qualified friendly identity and the upstream model ID so evidence
cannot confuse models with similar names.

## Runtime

One small provider table contains endpoint, key environment variable, upstream model ID,
Pi compatibility fields, and known token prices. Direct preflight, bounded Pi calls, and
weak-agent task containers all resolve through this table. Every non-verifier role in one
track continues to use the same configured provider and model.

Lab Pi calls mount a generated minimal `models.json` containing only the selected model.
The provider uses Pi's existing `openai-completions` implementation and its ordinary tool
loop. No alternate agent or patch path is added.

## Evidence and cost

Receipts save provider, friendly model, upstream model, token counts, request identifiers,
wall time, and sanitized failures. Known input, output, and cache-read rates are used for
catalog estimates. Because cache-write prices are unavailable, a response with nonzero
cache-write tokens is marked unpriced rather than assigned an invented price. Actual
gateway cost is recorded when the response exposes it.

## Verification

Offline tests cover pair validation, alias separation, provider-specific keys and URLs,
Pi catalog/command construction, redaction, and cost calculation. Paid tests require
`--live` and run separate direct and Pi write/read contracts for `deepseek-v4-flash` and
`qwen3.6-flash`, preserving evidence under `out/live-contracts/lab-provider/`. Persistent
provider or tool-use failure stops the gate.

After the provider table replaces their useful content, `skillrace_next/llm_call.py` and
`skillrace_next/model_prices.csv` are removed.
