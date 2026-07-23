# pi-base — the shared SkillRACE agent image

Operational runbook for the **level-1** image (Pi + Yunwu), shared across all
skills. Design rationale lives in [`docs/environments.md`](../../docs/environments.md);
Pi specifics in [`docs/pi-integration.md`](../../docs/pi-integration.md).

## What's in it
- `node:20-bookworm-slim` + `git` + `ripgrep`
- `@mariozechner/pi-coding-agent@0.73.1` (pinned; build-arg `PI_PKG`)
- one baked single-model catalog per track; the **API key is not baked** and is
  injected only at run time via `-e yunwu_key`

## Prereqs
- Docker, and `yunwu_key` exported in your shell.

## Build (level 1)
```bash
MODEL=glm-4.5-flash ./build.sh
MODEL=deepseek-v4-flash ./build.sh
# → skillrace/pi-base:0.73.1-<model>
```
First build is ~11 min (npm installs 210 packages); the BuildKit cache-mount makes
later rebuilds fast.

## Run one agent task (the external Runner stand-in)
```bash
./run_once.sh <model> <skill_dir> <out_dir> "<prompt>"
# e.g.
./run_once.sh glm-4.5-flash ../../skills/file-check out/demo \
  "Create greeting.txt with 'hi' and verify it."
```
Outputs in `<out_dir>/`: `session.jsonl` (the trace), `cost.json` (provider-credit
summary with no inferred USD), `stdout.txt`, and `stderr.txt`. Both selected models
have a successful archived multi-turn thinking/tool probe.

## Per-skill base (level 2) and per-test image (level 3)
```bash
python3 -m skillrace.d1_images --workers 3
docker build -t skillrace/run-<id>:built -f ../../skills/<skill>/seeds/<seed>.Containerfile ../../skills/<skill>/
```

## Where the image lives / portability
- Local Docker daemon (`/var/lib/docker`), tags
  `skillrace/pi-base:0.73.1-glm-4.5-flash` and
  `skillrace/pi-base:0.73.1-deepseek-v4-flash`.
- Portable archives may be created with `docker save`; the Dockerfile, exact catalogs,
  rate evidence, image IDs, and probe hashes are the source-of-truth metadata.

## Find the trace / cost
- Trace: `<out_dir>/session.jsonl` — tree-linked by `id`/`parentId`; each `assistant`
  message has `thinking` → `toolCall`, then a `toolResult`.
- Cost: token usage is recomputed with the dated Yunwu custom-credit rate card in the
  rolled-up `<out_dir>/cost.json`; Pi's zero catalog cost is never treated as billing.
