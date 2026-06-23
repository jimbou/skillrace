#!/usr/bin/env bash
# Build the SkillRACE pi-base image with versioned tags.
# Override the package/version to bump or migrate scope, e.g.:
#   PI_PKG=@earendil-works/pi-coding-agent@0.70.0 VERSION=0.70.0 ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

PI_PKG="${PI_PKG:-@mariozechner/pi-coding-agent@0.62.0}"
VERSION="${VERSION:-0.62.0}"

DOCKER_BUILDKIT=1 docker build \
  --build-arg PI_PKG="$PI_PKG" \
  -t "skillrace/pi-base:${VERSION}" \
  -t "skillrace/pi-base:latest" \
  -f Dockerfile.pi-base \
  --progress=plain \
  .

echo ""
echo "built: skillrace/pi-base:${VERSION}  (+ :latest)   pkg=${PI_PKG}"
docker images skillrace/pi-base
echo ""
echo "to save a portable copy:  docker save skillrace/pi-base:${VERSION} | gzip > pi-base-${VERSION}.tar.gz"
