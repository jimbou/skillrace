# pi-base — the shared SkillRACE agent image

Operational runbook for the **level-1** image (Pi + CloseAI), shared across all
skills. Design rationale lives in [`docs/environments.md`](../../docs/environments.md);
Pi specifics in [`docs/pi-integration.md`](../../docs/pi-integration.md).

## What's in it
- `node:20-bookworm-slim` + `git` + `ripgrep`
- `@mariozechner/pi-coding-agent@0.62.0` (pinned; build-arg `PI_PKG`)
- baked `models.closeai.json` (CloseAI provider; **API key NOT baked** — injected at
  run time via `-e CLOSE_API_KEY`)

## Prereqs
- Docker, and `CLOSE_API_KEY` exported in your shell (see `PI_CLOSEAI_GUIDE.md`).

## Build (level 1)
```bash
./build.sh                      # → skillrace/pi-base:0.62.0  + :latest
# bump / migrate scope later without editing the Dockerfile:
PI_PKG=@earendil-works/pi-coding-agent@0.70.0 VERSION=0.70.0 ./build.sh
```
First build is ~11 min (npm installs 210 packages); the BuildKit cache-mount makes
later rebuilds fast.

## Run one agent task (the external Runner stand-in)
```bash
./run_once.sh <model> <skill_dir> <out_dir> "<prompt>"
# e.g.
./run_once.sh qwen3.6-flash ../../skills/file-check out/demo \
  "Create greeting.txt with 'hi' and verify it."
```
Outputs in `<out_dir>/`: `session.jsonl` (the trace), `cost.json` (token+cost summary),
`stdout.txt`, `stderr.txt`. Traceable models: `qwen3.5-flash`, `qwen3.6-flash`, `glm-5`
(avoid `gemini-*`/`o4-mini` — no reasoning trace).

## Per-skill base (level 2) and per-test image (level 3)
```bash
docker build -t skillrace/<skill>:base -f ../../skills/<skill>/Containerfile.base ../../skills/<skill>/
docker build -t skillrace/run-<id>:built -f ../../skills/<skill>/seeds/<seed>.Containerfile ../../skills/<skill>/
```

## Where the image lives / portability
- Local Docker daemon (`/var/lib/docker`), tags `skillrace/pi-base:0.62.0` / `:latest`.
- Portable copy: `pi-base-0.62.0.tar` (this dir). Restore elsewhere with
  `docker load -i pi-base-0.62.0.tar`. The `.tar` is git-ignored (binary; the
  Dockerfile + `models.closeai.json` are the source of truth).

## Find the trace / cost
- Trace: `<out_dir>/session.jsonl` — tree-linked by `id`/`parentId`; each `assistant`
  message has `thinking` → `toolCall`, then a `toolResult`.
- Cost: per assistant message at `.message.usage.cost.total`, or the rolled-up
  `<out_dir>/cost.json`.
