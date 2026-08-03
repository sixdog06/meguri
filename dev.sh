#!/usr/bin/env bash
# 一键本地开发环境：db 走 compose 容器，后端/前端跑本地热重载进程。
# 发布前验证才用 ./start.sh（完整镜像构建）。
set -euo pipefail
cd "$(dirname "$0")"

docker compose up -d db

# Ctrl-C 时把两个后台进程一起收掉
trap 'kill 0' EXIT

.venv/bin/uvicorn app.main:app --reload --app-dir backend &
(cd frontend && npm run dev) &

wait
