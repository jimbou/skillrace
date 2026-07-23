# DeepSeek v3.2 development fallback probe

**Date:** 2026-07-13  
**Classification:** development compatibility evidence; not in the current headline catalog

## Scope

`deepseek-v3.2` was tested because the selected DeepSeek v4 route returned Yunwu HTTP 429
upstream-saturation responses. It is not currently in the dual-track schedules and has no
reviewed provider-credit rate card, D1 image lock, RQ1/RQ3 output root, or analysis input.
It is a candidate for the final hardcoded model inventory, not a replacement or a source
of interchangeable observations: if selected, it must receive the same dated rate,
image, direct/Pi preflight, protocol, schedule, and analysis artifacts before any
headline run. Until then, these observations remain development-only.

The one-model development catalog is
`experiments/development-pilots/2026-07-13/deepseek-v3.2-model.json`; it carries an
explicit `development_only: true` marker and is tested to remain outside
`EXPERIMENT_MODELS`.

## Results of this probe

1. A direct provider-minimal request returned HTTP 200 with provider model
   `deepseek-v3.2`.
2. A direct structured-realization request returned a parseable prompt/tail/sanity object;
   its tail passed the shared generated-tail policy.
3. A temporary development-only Pi overlay was built from the existing
   `argparse-scaffolder` construction image as
   `skillrace/argparse-scaffolder:base-deepseek-v3.2-dev`. A networkless audit confirmed
   Pi 0.73.1, the one-model v3.2 catalog, and the baked skill.
4. One short Pi task asked the mounted `argparse-scaffolder` skill to create
   `hello.txt` containing exactly `hello`. The session trace records a write, a successful
   read-back tool call returning `hello`, and a final successful response. Raw stdout,
   stderr, and trace are under
  `out/development-pilots/2026-07-13/deepseek-v3.2-pi-probe/`.
5. A later short direct call traversed the production wall-clock transport in 5.2 seconds
   and produced a durable development receipt with 10 prompt and 3 completion tokens.
   Its calculated cost was ⚡0.000029 under the provisional rate record below.
6. A fresh Pi 0.73.1 compatibility probe established that v3.2 can expose native
   reasoning while using tools through Yunwu. The custom endpoint initially omitted
   DeepSeek thinking because Pi could not infer the provider family from the Yunwu URL.
   Setting `compat.thinkingFormat` to `deepseek` made Pi send the thinking request and
   preserve `reasoning_content` between tool turns. Pi caps its default output allowance
   at 32,000 tokens, while Yunwu rejected the provider's default 32,768-token thinking
   budget; mapping Pi's `medium` level to provider `minimal` retained native reasoning
   within that cap. The final three assistant turns all contain reasoning blocks and the
   write/read tool sequence completed without errors. The successful trace is
   `out/development-pilots/2026-07-13/glm-v32-preflight-v1/deepseek-v3.2-thinking-v3/session.jsonl`
   (SHA-256 `07d4a61f4208ca4ea4db122b574633c947ae596049751791bba1239ed8e8caba`).
   The two earlier rejected configurations are retained beside it as diagnostics and
   are not successful evidence.
7. The audited development image used for the current JSON-parser campaign smoke is
   `skillrace/json-parser:base-deepseek-v3.2-dev`, image ID
   `sha256:b43d6f7f93fea33736b9c7e9a394e60a8131b362828350db7874d98ef4c73432`.
   Its catalog remains explicitly development-only. Because v3.2 now provides native
   reasoning, the conditional `qwen3.5-flash` fallback was not used.

The provider/Pi trace reports token usage. The initial compatibility probe predates the
provisional rate record and therefore records cost as **unknown**, never zero. Later
development calls use the dated provisional rate record; this is still not archival
headline evidence. It is engineering evidence that a development candidate is available,
not evidence about SkillRACE, the baselines, defect yield, repair, or RQ3 generation.
