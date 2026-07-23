#!/usr/bin/env bash
# Build one model-frozen SkillRACE Pi image with a versioned track tag.
# Override the package/version to bump or migrate scope, e.g.:
#   PI_PKG=@earendil-works/pi-coding-agent@0.70.0 VERSION=0.70.0 ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

PI_PKG="${PI_PKG:-@mariozechner/pi-coding-agent@0.73.1}"
VERSION="${VERSION:-0.73.1}"
MODEL="${MODEL:-glm-4.5-flash}"

case "$MODEL" in
  glm-4.5-flash|glm-4.5|glm-4.5-air|glm-4.7|grok-4.3|grok-4-1-fast-reasoning|qwen3.5-plus|qwen3-coder-flash|qwen3-coder-480b-a35b-instruct|deepseek-v4-flash|deepseek-v3.2) ;;
  *) echo "unsupported Yunwu model: $MODEL" >&2; exit 2 ;;
esac

MODEL_CONFIG="models.yunwu.${MODEL}.json"
TAG="skillrace/pi-base:${VERSION}-${MODEL}"

DOCKER_BUILDKIT=1 docker build \
  --build-arg PI_PKG="$PI_PKG" \
  --build-arg TRACK_MODEL="$MODEL" \
  --build-arg MODEL_CONFIG="$MODEL_CONFIG" \
  -t "$TAG" \
  -f Dockerfile.pi-base \
  --progress=plain \
  .

echo ""
echo "built: ${TAG}   pkg=${PI_PKG}"
docker images skillrace/pi-base
echo ""
echo "to save a portable copy:  docker save ${TAG} | gzip > pi-base-${VERSION}-${MODEL}.tar.gz"
