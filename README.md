# Meguri

圣地巡礼行程规划 Agent（对话式 Web 应用）。领域术语见 `CONTEXT.md`，架构决策见 `docs/adr/`，产品 spec 见 GitHub issue #1。

## 一键启动（Docker）

```bash
./start.sh
```

启动后访问 http://localhost:8080 （web = nginx 静态前端 + `/api` 反代到后端）。

## 本地开发

LLM 配置（真实模型）：把 key 写进仓库根的 `.env.local`（已 gitignore，勿提交）：

```bash
MEGURI_OPENAI_API_KEY=...
MEGURI_OPENAI_BASE_URL=https://api.kimi.com/coding/v1
MEGURI_OPENAI_MODEL=kimi-for-coding
```

`adapter_mode`：`live` 经 LangChain 适配层调真实模型（对话理解/工具调用/生成式讲解），缺 key 会明确报错；`fake` 用 FakeLLMGateway（测试专用，离线）。`dev.sh` 默认 live。

后端（Python 3.11+）：

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd backend && ../.venv/bin/python -m pytest   # 行为测试经 FastAPI TestClient（HTTP 缝）
.venv/bin/uvicorn app.main:app --reload --app-dir backend
```

注意：圣地数据源默认 `seichi_mode=live`，裸跑 dev 会访问外部网络（api.anitabi.cn）；要完全离线可设 `MEGURI_SEICHI_MODE=fake`（测试已默认 fake）。

**数据层架构（用户拍板，最终版）**：
- **本地 JSON 的唯一职责是 ID↔名字映射**：`python -m app.ingest_bangumi` 用 Bangumi v0 API（自定义 UA、限速 ≤2 req/s、按年 checkpoint 断点续传）拉 1990 年后全部动画 → `data/works/anime-1990plus.json`（`{id, name, name_cn, air_date}`，summary 保留供 RAG 语料）。运行流程：用户 prompt →（LLM）解析出作品名 → 查本地映射拿 subjectID → **实时**调 anitabi `/bangumi/{id}/lite` 拿圣地数据。
- **两种显式结果**：anitabi 调用失败（权限/网络/403/超时/非 JSON 间隙页）→ `SeichiSourceUnavailable` → **503 + "圣地数据服务暂时不可用"**（不降级本地数据包）；anitabi 成功但无数据 → 结构化 `notice` + 回复如实转述 **"这部作品没有圣地巡礼数据"**（非错误，也区别于"还在加载"）。前端两种情形分别有 toast 提示。

交通（#6）：`dev.sh` 默认 `MEGURI_TRANSIT_MODE=live`，走本地 OTP；OTP 未启动时 Navigator 自动降级为估算段（leg 带"降级"标记），不会报错。

## OTP 交通图（宇治/京都）

一次性构建 routing graph（幂等，可重复执行）：

```bash
otp/download.sh   # 下载 kansai OSM → osmium 裁剪京都/宇治 → otp/data/Kyoto.osm.pbf
otp/build.sh      # docker 构建 graph.obj 并校验（吃内存，docker VM 建议 ≥ 6GB）
docker compose up -d otp   # 起 OTP 服务（:8081）
```

GTFS（公共交通换乘/时刻表）：京都市営バス/地下鉄的 GTFS-JP 只发布在公共交通オープンデータセンター（ODPT），需免费注册拿 consumerKey（见 `otp/download.sh` 头部注释），把 zip 放进 `otp/data/` 后 `otp/build.sh --force` 重建即可。没有 GTFS 时 graph 只含路网：步行/车程为真实 OSM 路网耗时，换乘查询返回"未覆盖"降级。已知覆盖缺口：宇治的 JR 奈良线、京阪宇治线（无公开 GTFS）及京都市营巴士/地铁（需注册）。

## RAG 语料库（#8）

语料（bgm.tv 作品条目 + anitabi 地标描述）灌进同库 pgvector（`corpus_chunks` 表），经 `CorpusStore` 统一检索接口访问；`dev.sh` 默认 `MEGURI_CORPUS_MODE=live`。embedding 默认与 chat LLM 同端点，也可用 `MEGURI_EMBEDDING_BASE_URL`/`MEGURI_EMBEDDING_API_KEY` 独立指定——**推荐本地 Ollama bge-m3**（免费、离线、中日文混合检索好）：

```bash
brew install ollama && ollama serve &   # 起本地服务（:11434）
ollama pull bge-m3                      # 约 1.2GB，一次性
# .env.local 加：MEGURI_EMBEDDING_BASE_URL=http://localhost:11434/v1
#   MEGURI_EMBEDDING_API_KEY=ollama  MEGURI_EMBEDDING_MODEL=bge-m3
#   MEGURI_EMBEDDING_DIM=1024（改维度必须 DROP TABLE corpus_chunks 后重启重建）
```

任何 key 都不配时用确定性哈希向量（检索链路真实、向量无语义，仅供开发/测试）。灌库幂等：

```bash
MEGURI_CORPUS_MODE=live .venv/bin/python -m app.rag.ingest --work 吹响吧！上低音号
```

注意 anitabi 部分需要能访问 api.anitabi.cn 的网络；灌库走脚本而非运行时每请求现灌。语料事实：bgm.tv 的 summary 是真实作品简介文本；anitabi 的地标数据**没有自由文本/评论字段**，anitabi 语料是"元数据文本化"（名称+出处集数拼句），不是地标描述原文。检索有相似度阈值（`MEGURI_CORPUS_MIN_SCORE`，默认 0.6 按哈希向量标定，真 embedding 需按实测调低）：相关度不达标的语料不会成为讲解 citation。

## 评测（#10，不进 CI 门禁）

自研评测 harness 在 `eval/`（pytest 驱动，但与 `backend/tests` 行为测试分离——`backend/pytest.ini` 的 testpaths 不含 eval，需显式运行）：

```bash
.venv/bin/python -m pytest eval/ -v -s   # 需要 5433 的 Postgres（compose db）
```

- **golden 数据集** `eval/datasets/*.jsonl`，按 Agent 组织（scout/planner/navigator/storyteller/e2e），内容用真实已抓取数据构造（anitabi 京吹宇治地标、bgm.tv 条目）。扩数据集 = 追加 JSONL 行（字段见 `eval/harness.py` 的读取处）。
- **指标**：Scout 检索命中率（期望 id 子集的命中比例，数据集每例带 `rationale` 说明期望的独立依据）；Planner 规则（天数正确/每天非空/全覆盖/最近邻不劣于固定种子随机基线）；Navigator 时间可行性（checks 完整/到达时刻单调）；Storyteller **引用保真 citation_fidelity**（讲解文本确实是其 citation chunk 的摘录、且该 chunk 是该圣地的实际检索结果——对检索式拼装实现近乎恒真；真正的生成式事实性判断是真 LLM judge 的事，已 deferred）；e2e 检查清单（回复/行程/legs/checks/讲解/trace 覆盖，RuleJudge 布尔评分）。
- **JudgeProvider**（`eval/judge.py`）：judge(rubric, output) → score+reason；离线用确定性 `RuleJudge`（规则式打分），**真 LLM judge（OpenAIJudge）当前是 stub**，配置位 `MEGURI_OPENAI_*`。
- **tracing 消费**：orchestrator 埋点 `loop_step/llm_call/tool_call/pipeline_stage`；`JsonlTracer`（`app/agents/tracing.py`）把 trace 导出 JSONL——e2e 评测里真实消费（落临时文件回放后校验事件序列），也可 dependency override `get_tracer` 接入。
- 数据集事实：storyteller 案例的手写描述句 chunk 是人工夹具（anitabi 无地标自由文本，真实语料为"元数据文本化"，见各 jsonl 的 `_note`/`rationale` 字段）。

前端（Node 22+）：

```bash
cd frontend
npm install
npm run dev          # vite dev server，/api 代理到 localhost:8000
npm run type-check   # vue-tsc
```
