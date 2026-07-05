#!/usr/bin/env bash
# Build the D2 base image `skillrace/skillgen-base:latest`.
#
# It provides python3 + pytest + git + the `pi` agent harness with an empty /workspace,
# and is derived from the existing `skillrace/fix-failing-test:base` so it needs NO
# network (no apt/pip). If you are rebuilding from scratch with network available,
# base it on skillrace/pi-base:<ver> and `apt-get install -y python3 && pip install pytest`
# instead of the FROM below.
set -eux
d=$(mktemp -d)
cat > "$d/Dockerfile" <<'DOCK'
FROM skillrace/fix-failing-test:base
RUN rm -rf /workspace /skills/fix-failing-test && mkdir -p /workspace /skills
WORKDIR /workspace
RUN git init -q -b main \
 && git config --global user.email base@skillrace.local \
 && git config --global user.name skillrace \
 && printf '__pycache__/\n*.pyc\n' > .gitignore \
 && git add -A && git commit -q -m "skillgen base: empty workspace"
DOCK
docker build -t skillrace/skillgen-base:latest "$d"
rm -rf "$d"
echo "built skillrace/skillgen-base:latest"
