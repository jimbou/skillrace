#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PARENT_TAG="${PARENT_TAG:-skillrace/pi-base:0.73.1-glm-4.5-flash}"
BOOTSTRAP_TAG="${BOOTSTRAP_TAG:-skillrace/skillgen-base:python3.11-pytest7.2.1-bootstrap}"
OUTPUT_TAG="${OUTPUT_TAG:-skillrace/skillgen-base:0.73.1-construction}"
expected_parent=$(python3 -c 'import json; print(json.load(open("base-image.lock.json"))["parent_image_id"])')
actual_parent=$(docker image inspect --format '{{.Id}}' "$PARENT_TAG")
if [[ "$actual_parent" != "$expected_parent" ]]; then
  echo "refusing build: $PARENT_TAG does not match base-image.lock.json" >&2
  exit 2
fi
expected_bootstrap=$(python3 -c 'import json; print(json.load(open("base-image.lock.json"))["python_bootstrap_image_id"])')
actual_bootstrap=$(docker image inspect --format '{{.Id}}' "$BOOTSTRAP_TAG")
if [[ "$actual_bootstrap" != "$expected_bootstrap" ]]; then
  echo "refusing build: $BOOTSTRAP_TAG does not match base-image.lock.json" >&2
  exit 2
fi

DOCKER_BUILDKIT=1 docker build \
  --build-arg PI_RUNTIME_IMAGE="$PARENT_TAG" \
  --build-arg PYTHON_BOOTSTRAP_IMAGE="$BOOTSTRAP_TAG" \
  --file Dockerfile.skillgen-base \
  --tag "$OUTPUT_TAG" \
  --progress=plain \
  .

docker run --rm --network=none "$OUTPUT_TAG" bash -lc \
  'set -euo pipefail; python3 --version; python3 -m pytest --version; pi --version; git -C /workspace status --porcelain'
