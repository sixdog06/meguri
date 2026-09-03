# Meguri（巡る）

对话式动画圣地巡礼规划 Agent。你用中文说"我想去《轻音少女》的京都巡礼，三天"，系统检索真实圣地数据（anitabi.cn），规划出按天组织、带真实交通和每站讲解的行程，画在地图上。

**核心设计哲学**：LLM 只做它不可替代的事——理解用户意图和写文案；所有可验证的事实（地点、距离、时刻）都走确定性代码和真实数据源，LLM 碰不到。

## 功能展示

以轻音少女为例——输入"我想去轻音少女的京都巡礼，三天"：

![对话 + 行程面板 + 地图](docs/screenshots/app-overview.png)

- 第一季、第二季的 124 处圣地**合并规划**成一个行程（每站保留出处作品标记）
- 剧场版的 51 处在欧洲，被"京都"过滤后**在回复正文中如实告知**（"本次未包含，以后可单独规划"），不静默丢弃
- 地图上按天上色的标点 + 每天的路线；行程面板里每站带对照截图、到达时刻、讲解和可点击的来源署名链接（CC BY-NC-SA 要求的标注）
- 行程支持编辑（增删站点/换序/换天），编辑后自动重校验交通和时刻
- 可导出 PDF；每天路线可一键在 Google 地图打开

## 快速开始（一键部署）

前提：Docker（compose）。**需要一个 OpenAI 兼容的 LLM API key**（默认配的是 kimi）：

```bash
cp .env.example .env.local   # 编辑，填入 MEGURI_OPENAI_API_KEY
./start.sh                   # 构建并启动 db + backend + web
```

访问 http://localhost:8080 （web = nginx 静态前端 + `/api` 反代到后端）。

首次启动后灌一次作品目录（作品名解析的数据源，19144 条 Bangumi 动画索引）：

```bash
docker compose exec backend python -m app.rag.ingest_works
```

没有 key 时系统也能启动，但对话会在调用 LLM 时明确报"模型服务暂时不可用"。

## 原理

### 一次 Agent Loop 怎么走

编排核心是一个自研的最小 ReAct 循环：

1. 用户消息先落库（`messages` 表），SSE 推 `received`
2. 组装上下文：system prompt（角色 + 动态工具清单 + 线格式约定）+ 该会话全量历史
3. 调 LLM。输出按首字符分类：`{` 开头 = 工具调用（缓冲不上屏），否则 = 最终回复（逐段 SSE 流式上屏）
4. 工具调用 → 执行 → 观察结果以 `[工具观察结果]` 前缀回灌 messages，进入下一轮；最多 5 轮兜底
5. 最终回复落库（assistant 消息 + 结构化 payload），行程快照追加到 `itineraries` 表，SSE 推 `done`

### 两个工具的输入输出

模型只有两把工具，参数和产出都是约定好的线格式：

**search_seichi（Scout：圣地检索）**

输入（模型输出的 tool_call）：

```json
{"type": "tool_call", "name": "search_seichi",
 "args": {"ani_name": "轻音少女", "area": "京都"}}
```

输出（给模型的观察值，同时也是给前端的结构化 payload）：

```json
{
  "candidates": [{"id": "abc123", "name": "豊郷小学校旧校舎群", "work": "轻音少女",
                  "area": "滋賀県", "lat": 35.20, "lng": 136.22,
                  "image": "https://...", "ep": 4, "ep_seconds": 812,
                  "origin": "Anitabi@卜卜口", "origin_url": "https://anitabi.cn/"}],
  "by_work": {"轻音少女": 48, "轻音少女 第二季": 76},
  "out_of_area": [{"work": "轻音少女 剧场版", "city": "欧洲", "count": 51}],
  "note": "out_of_area 里的作品在本次地区之外有巡礼点，请在回复中告知用户"
}
```

内部链路：`ani_name` → `anime_works` 表解析 subjectID（子串精确匹配优先，pg_trgm 相似度兜底错字，多作品命中全返回）→ 逐作品实时调 anitabi 取地标（故障按作品回退本地离线包并显式提示）→ 地区过滤 + 合并。

**作品名解析为什么用 pg_trgm**：用户输入不可能总是标准全名——会少字、带错字（"吹响吧上低音号" 少个标点）。pg_trgm 的做法是把字符串切成连续三字符窗口（trigram）的集合，两个字符串的相似度 = 共有 trigram 占的比例：错一个字只影响相邻两三个窗口，其余全部重合，所以错字依然高分命中；而"京吹"和"吹响吧！上低音号"没有任何公共 trigram，如实不命中（俗名归一化是上游 LLM 的职责，检索层不冒充）。它不查词表、不分词，中文/日文/混排天然适用。索引上，GIN 把每个名字的 trigram 建成"trigram → 行"的倒排表，查询时按 trigram 集合直接取候选行，不扫全表。

**plan_itinerary（Planner 全家桶：检索 → 聚类 → 交通 → 讲解）**

输入：

```json
{"type": "tool_call", "name": "plan_itinerary",
 "args": {"ani_name": "轻音少女", "area": "京都", "days": 3}}
```

输出（行程快照，落 `itineraries` 表 + 随响应返回前端）：

```json
{
  "work": "轻音少女", "area": "京都", "day_count": 3,
  "days": [{
    "day": 1,
    "seichi": [{"id": "abc123", "name": "...", "lat": 35.01, "lng": 135.76, "work": "轻音少女"}],
    "legs": [{"from_id": "abc123", "to_id": "def456", "mode": "transit",
              "duration_minutes": 22, "estimate": false, "degraded": false}],
    "checks": [{"seichi_id": "abc123", "arrive_time": "09:00"}],
    "narrations": [{"seichi_id": "abc123", "text": "《轻音少女》取景地「…」，出自第 4 集…",
                    "citation": {"source": "Anitabi@卜卜口", "url": "https://anitabi.cn/"}}]
  }]
}
```

内部管线：k-means 地理聚类切天 → 天内最近邻排序 → OTP 真实交通替换估算段 → 推算各站到达时刻 → 站点元数据生成讲解。这条管线全是确定性代码，地点、距离、时刻等可验证的事实不经过模型。

### 故障语义（三级显式区分）

anitabi 故障且有离线包 → 降级离线数据包 + 提示"可能不是最新"；故障且无包 → 503"圣地数据服务暂时不可用"；成功但该作品无数据 → 200 + "这部作品没有圣地巡礼数据"。可以降级，但绝不静默冒充实时数据。

### 评测

golden 数据集 + 确定性规则判卷，显式运行（与后端行为测试分离）：

```bash
.venv/bin/python -m pytest eval/ -v -s   # 需要 5433 的 Postgres（compose db）
```

机制：每个 case 用假外部世界（脚本化 LLM + 内存假数据）驱动**完整真管线**（HTTP → ReAct 循环 → 工具 → 落库），对照标准答案打分。维度：Scout 检索命中率 / Planner 四规则（天数正确、每天非空、全覆盖、最近邻优于随机基线）/ Navigator 时间可行性（时刻单调）/ Storyteller 讲解接地（含站名、署名与站点 origin 一致）/ e2e 六项清单（含 trace 可观测性）/ work_resolve 作品名解析 8 例（打真实 PG 的 anime_works 表，标定 pg_trgm 阈值用）。数据集每例都写明期望值的独立依据；判卷规则自身也有元测试，防止"规则永远通过"的假把式。

诚实边界：真 LLM judge（校验生成内容是否编造事实）还是 stub；LLM 的工具决策质量（真实模型表现）不在评测范围。

