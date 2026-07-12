#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

expected_parent=$(python3 -c 'import json; print(json.load(open("base-image.lock.json"))["parent_image_id"])')
actual_parent=$(docker image inspect --format '{{.Id}}' skillrace/pi-base:0.62.0)
if [[ "$actual_parent" != "$expected_parent" ]]; then
  echo "refusing build: skillrace/pi-base:0.62.0 does not match base-image.lock.json" >&2
  exit 2
fi

DOCKER_BUILDKIT=1 docker build \
  --file Dockerfile.skillgen-base \
  --tag skillrace/skillgen-base:2026-07-11 \
  --tag skillrace/skillgen-base:latest \
  --progress=plain \
  .

docker run --rm skillrace/skillgen-base:2026-07-11 bash -lc \
  'set -euo pipefail; python3 --version; python3 -m pytest --version; pi --version; git -C /workspace status --porcelain'
