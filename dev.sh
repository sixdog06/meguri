#!/usr/bin/env bash
# 一键本地开发环境：db 走 compose 容器，后端/前端跑本地热重载进程。
# 发布前验证才用 ./start.sh（完整镜像构建）。
set -euo pipefail
cd "$(dirname "$0")"

docker compose up -d db

export MEGURI_DATABASE_URL=postgresql+psycopg://meguri:meguri@localhost:5433/meguri
# LLM/交通只有唯一实现（真实模型 / 本地 OTP），无模式开关；
# LLM key 在 .env.local（已 gitignore）
# 圣地数据源：live = anitabi 实时（故障显式 503 / 本地包兜底）；
# 完全不触网可改 MEGURI_SEICHI_MODE=file（本地数据包）
export MEGURI_SEICHI_MODE=live
# debug 模式：anitabi 不触网，返回固定罐头数据（K-ON! 京都切片）——
# 问任何作品都返回轻音的点，仅离线调试时显式开启：MEGURI_DEBUG_MODE=true ./dev.sh
export MEGURI_DEBUG_MODE=${MEGURI_DEBUG_MODE:-false}
# 交通走本地 OTP（otp/download.sh + otp/build.sh 先建 graph，
# docker compose up -d otp 起服务；OTP 未起时 Navigator 自动降级为估算段）
# 作品目录灌库（works 解析依赖）：.venv/bin/python -m app.rag.ingest_works

# Ctrl-C 时把两个后台进程一起收掉
trap 'kill 0' EXIT

.venv/bin/uvicorn app.main:app --reload --app-dir backend &
(cd frontend && npm run dev) &

wait
