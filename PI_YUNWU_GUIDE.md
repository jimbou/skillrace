# Using Pi with Yunwu

SkillRACE now uses Yunwu for both direct model calls and Pi agent runs. Export the
key under this exact, deliberately lowercase name:

```bash
export yunwu_key='...'
```

Each track image bakes exactly one of
[`models.yunwu.glm-4.5-flash.json`](./images/pi-base/models.yunwu.glm-4.5-flash.json)
or [`models.yunwu.deepseek-v4-flash.json`](./images/pi-base/models.yunwu.deepseek-v4-flash.json).
Both configure Pi's OpenAI-compatible provider as `yunwu` at `https://yunwu.ai/v1`.
Build both pinned Pi 0.73.1 images with:

```bash
MODEL=glm-4.5-flash images/pi-base/build.sh
MODEL=deepseek-v4-flash images/pi-base/build.sh
```

The normal runner needs no provider flags beyond its existing model selection; it
invokes Pi as `--provider yunwu` and passes only `yunwu_key` into the trusted
agent execution. To make exactly one small connectivity call before a paid run:

```bash
python3 scripts/yunwu_hello.py --model glm-4.5-flash
```

The diagnostic never prints the key. The reviewed direct and Pi probes for both track
models are already archived under `experiments/provider-evidence/yunwu-2026-07-12/`.
That directory also freezes Yunwu's public custom-credit (`⚡`) rate evidence. Do not
label these provider credits as dollars or reuse an old provider price.
