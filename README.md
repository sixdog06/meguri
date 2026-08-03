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

交通与开放时间（#6）：`dev.sh` 默认 `MEGURI_TRANSIT_MODE=live` / `MEGURI_HOURS_MODE=live`，走本地 OTP 与 Overpass；OTP 未启动时 Navigator 自动降级为估算段（leg 带"降级"标记），不会报错。注意 `hours_mode=live` 会访问外部网络（overpass-api.de，查 OSM opening_hours）；离线可设 `MEGURI_HOURS_MODE=fake`（测试已默认 fake）。

## OTP 交通图（宇治/京都）

一次性构建 routing graph（幂等，可重复执行）：

```bash
otp/download.sh   # 下载 kansai OSM → osmium 裁剪京都/宇治 → otp/data/Kyoto.osm.pbf
otp/build.sh      # docker 构建 graph.obj 并校验（吃内存，docker VM 建议 ≥ 6GB）
docker compose up -d otp   # 起 OTP 服务（:8081）
```

GTFS（公共交通换乘/时刻表）：京都市営バス/地下鉄的 GTFS-JP 只发布在公共交通オープンデータセンター（ODPT），需免费注册拿 consumerKey（见 `otp/download.sh` 头部注释），把 zip 放进 `otp/data/` 后 `otp/build.sh --force` 重建即可。没有 GTFS 时 graph 只含路网：步行/车程为真实 OSM 路网耗时，换乘查询返回"未覆盖"降级。已知覆盖缺口：宇治的 JR 奈良线、京阪宇治线（无公开 GTFS）及京都市营巴士/地铁（需注册）。

前端（Node 22+）：

```bash
cd frontend
npm install
npm run dev          # vite dev server，/api 代理到 localhost:8000
npm run type-check   # vue-tsc
```
