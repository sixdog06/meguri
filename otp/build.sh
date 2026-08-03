#!/usr/bin/env bash
# OTP graph 构建 + 校验（幂等）：otp/data 里的 pbf + GTFS zip → graph.obj。
# 已存在 graph.obj 时跳过（加 --force 强制重建）。
set -euo pipefail
cd "$(dirname "$0")"

IMAGE=opentripplanner/opentripplanner:2.6.0

if [ -s data/graph.obj ] && [ "${1:-}" != "--force" ]; then
  echo "skip: data/graph.obj 已存在（--force 强制重建）"
else
  echo "构建 OTP graph（吃内存，建议 docker VM >= 6GB）…"
  docker run --rm -v "$PWD/data:/var/opentripplanner" "$IMAGE" --build --save
fi

# 校验：graph.obj 存在且非空
test -s data/graph.obj
echo "OK: graph.obj $(du -h data/graph.obj | cut -f1)"
