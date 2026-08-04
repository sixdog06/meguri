#!/usr/bin/env bash
# 一键本地开发环境：db 走 compose 容器，后端/前端跑本地热重载进程。
# 发布前验证才用 ./start.sh（完整镜像构建）。
set -euo pipefail
cd "$(dirname "$0")"

docker compose up -d db

export MEGURI_DATABASE_URL=postgresql+psycopg://meguri:meguri@localhost:5433/meguri
# 圣地数据源：本机 IP 被 Cloudflare 间歇性封在 api.anitabi.cn，默认用离线
# 数据包（data/seichi/，真实 anitabi 切片）；网络正常处改 MEGURI_SEICHI_MODE=live
export MEGURI_SEICHI_MODE=file
# 交通/开放时间走本地 OTP（otp/download.sh + otp/build.sh 先建 graph，
# docker compose up -d otp 起服务；OTP 未起时 Navigator 自动降级为估算段）
export MEGURI_TRANSIT_MODE=live
export MEGURI_HOURS_MODE=live
# RAG 语料走同库 pgvector（灌库：.venv/bin/python -m app.rag.ingest --work 吹响吧！上低音号）
export MEGURI_CORPUS_MODE=live

# Ctrl-C 时把两个后台进程一起收掉
trap 'kill 0' EXIT

.venv/bin/uvicorn app.main:app --reload --app-dir backend &
(cd frontend && npm run dev) &

wait
