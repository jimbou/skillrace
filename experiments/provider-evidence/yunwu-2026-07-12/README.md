# Yunwu rate-card evidence — 2026-07-12

`rate-card.json` is the pre-result, machine-checkable extract used by both complete
experiment tracks. It records the exact public endpoints, retrieval time, byte counts,
and SHA-256 hashes of the responses observed at retrieval. It then preserves only the
status fields, default-group ratio, selected model records, and pricing formula needed
for the experiment; the large provider responses are not vendored.

Yunwu's public pricing UI computes quota-type-0 input and output rates from model,
completion, and group ratios. With the default group ratio of 1, the observed rates are
`⚡0.02/⚡0.08` per million input/output tokens for `glm-4.5-flash`, and
`⚡1.00/⚡2.00` for `deepseek-v4-flash`. DeepSeek advertises a cache ratio of 0.02,
giving `⚡0.02` per million cache-read tokens. GLM advertises no distinct cache ratio,
so accounting conservatively uses its normal input rate.

The symbol `⚡` is the provider's custom display currency. Purchase price and exchange
fields are retained as provenance only. They are not enough to establish the experiment
account's effective USD cost, so the artifact never labels these rates or receipts as
USD. Headline reporting uses provider credits and may separately report actual account
top-ups if independently documented.

Run the offline validator with:

```bash
python3 -m skillrace.provider_evidence \
  experiments/provider-evidence/yunwu-2026-07-12/rate-card.json
```

The exact response hashes are evidence of what was observed, not promises that a live
endpoint remains byte-identical later. A later provider change requires a new dated
snapshot and explicit decision; it must not silently mutate this record.
