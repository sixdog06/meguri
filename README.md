# Meguri

圣地巡礼行程规划 Agent（对话式 Web 应用）。领域术语见 `CONTEXT.md`，架构决策见 `docs/adr/`，产品 spec 见 GitHub issue #1。

## 一键启动（Docker）

```bash
./start.sh
```

启动后访问 http://localhost:8080 （web = nginx 静态前端 + `/api` 反代到后端）。

## 本地开发

后端（Python 3.11+）：

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd backend && ../.venv/bin/python -m pytest   # 行为测试经 FastAPI TestClient（HTTP 缝）
.venv/bin/uvicorn app.main:app --reload --app-dir backend
```

注意：圣地数据源默认 `seichi_mode=live`，裸跑 dev 会访问外部网络（api.anitabi.cn / api.bgm.tv）；要完全离线可设 `MEGURI_SEICHI_MODE=fake`（测试已默认 fake）。

前端（Node 22+）：

```bash
cd frontend
npm install
npm run dev          # vite dev server，/api 代理到 localhost:8000
npm run type-check   # vue-tsc
```
