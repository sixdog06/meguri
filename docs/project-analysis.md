# Meguri 项目剖析（面试讲解版）

> 生成于 2026-08-26。面向面试口述组织：核心叙事、设计决策、可主动交底的权衡。
> 代码级缺口清单见仓库根 ANALYSIS.md（本地参考，未入库）。

## 一句话

**Meguri 是一个对话式圣地巡礼行程规划 Agent**：用户用自然语言说"我想去宇治巡礼《吹响吧！上低音号》，玩两天"，系统输出可执行的多日行程——按天组织的圣地序列、真实交通衔接与时刻、地图可视化、每个取景地的作品背景讲解（带引用）。

## 架构总览

```
浏览器（Vue3 + Leaflet 地图 + SSE 流式）
  └─ nginx / vite proxy
      └─ FastAPI（backend/app）
          └─ Orchestrator（自研 ReAct 循环，最多 5 轮）
              ├─ SearchSeichiTool  → Scout：anitabi 实时检索圣地
              └─ PlanItineraryTool → Planner（确定性聚类切天）
                                   → Navigator（OTP 交通校验，确定性）
                                   → Storyteller（RAG 讲解生成）
          └─ Postgres（pgvector）：会话/消息/行程快照 + RAG 语料
docker-compose 四服务：db / backend / otp / web
```

技术栈：Python 3.11+ / FastAPI / SQLAlchemy / LangChain（仅作 LLM 网关）/ Vue 3 + Vite + Leaflet / OpenTripPlanner 2.6 / pgvector。

## 核心叙事：五个角色，但不是五个 LLM Agent

按"幻觉容忍度"决定每个角色用 LLM 还是确定性代码：

| 角色 | 职责 | 实现 | 为什么 |
|---|---|---|---|
| Orchestrator | 对话状态、澄清需求、分发任务 | LLM（自研 ReAct，ADR-0002） | 对话理解必须 LLM |
| Scout | "某作品在某区域有哪些圣地" | anitabi API 检索 | 事实检索，不需要生成 |
| Planner | 地理聚类切天、天内排序 | 纯确定性（k-means + 最近邻 + 2-opt） | 排序质量可验证，LLM 排路线不可靠 |
| Navigator | 真实交通段替换、时刻推算 | 确定性模块，被工具编排 | **交通语义零幻觉**——LLM 会编造不存在的换乘 |
| Storyteller | 取景地讲解 | RAG：检索不到语料就不产出 | 讲解必须可引用（citation），零幻觉 |

金句："**LLM 只做它不可替代的事——理解用户意图和写讲解；所有可验证的事实（地点、距离、时刻）都走确定性管线和真实数据源，LLM 碰不到。**"

## 亮点二：端口-适配器 + 模式矩阵，每层都可离线替换

所有外部依赖抽象成端口（`backend/app/adapters/ports.py`：LLMGateway / SeichiRepository / TransitClient / CorpusStore / EmbeddingProvider），每个端口有 live/fake 实现，`MEGURI_*_MODE` 环境变量切换：

- **LLM**：live = LangChain ChatOpenAI（30s 超时、连接错误重试 2 次、耗尽抛 503）；fake = 关键词启发式
- **圣地数据**：live = anitabi 实时（curl_cffi 伪装 TLS 指纹绕 Cloudflare）；file = 离线数据包；fake = 测试罐头
- **交通**：live = 本地 OTP；OTP 挂了 Navigator 自动降级为 haversine 估算段（leg 带"降级"标记），不报错
- **RAG**：pgvector 余弦检索 + 相似度阈值，无 embedding key 时用确定性哈希向量

收益：测试全 fake 离线秒跑（`backend/tests` 经 TestClient 走 HTTP 缝）；演示可按网络条件逐层降级；每层故障语义显式。

## 亮点三：显式失败语义，不静默降级

- anitabi 调用失败（403/超时/被封）→ **503 + "圣地数据服务暂时不可用"**，故意不降级本地数据包——本地包可能是旧数据，静默降级会让用户拿到错误事实还以为是真的
- anitabi 成功但该作品无数据 → 结构化 `notice`，回复如实说"这部作品没有圣地巡礼数据"——**区分"没有数据"和"服务故障"和"还在加载"三种状态**
- 前端对两种情形有不同 toast

## 端到端流程

1. 用户消息 → `POST /conversations/{id}/messages`，Orchestrator 落库后拼 system prompt + 全量历史
2. ReAct 循环：LLM 输出一行 JSON = 工具调用，纯文本 = 最终回复；首字符是 `{` 就缓冲不上屏，否则 token 级流式推 SSE `reply_chunk`
3. `PlanItineraryTool` 内部串起整条管线：检索 → k-means 聚类切天 → OTP 耗时矩阵 2-opt 重排天内顺序 → OTP 真实段替换 + 09:00 起每站 45 分钟推算时刻 → RAG 检索语料生成讲解（top-1 作 citation）
4. 最终回复落库，行程写快照表，SSE 发 `done`
5. 编辑流程：四种操作（加/删/换序/换天）纯结构变换 → 重校验 → **保留未受影响站的讲解**，只给新站补讲解

## 评测体系

`eval/` 自研 harness，golden 数据集按 Agent 组织（scout/planner/navigator/storyteller/e2e），RuleJudge 确定性打分：Scout 检索命中率、Planner 不劣于固定种子随机基线、Navigator 时刻单调性、**Storyteller 引用保真**（讲解确实是其 citation chunk 的摘录）。LLM judge 留了配置位但 defer——规则评测的边界明确，生成式事实性判断需要真 judge。

## 可主动交底的权衡

- **自研 ReAct 而不是 LangChain Agent 框架**（ADR-0002）：框架的黑盒循环不好控制流式和落库时机，自研 200 行换完全掌控——LangChain 只用在它真正有价值的地方（模型网关）
- **单模型**：kimi-for-coding 兼任对话理解/工具调用/讲解生成，分层混用留了配置位没做
- **OTP 只建了京都/宇治路网 graph**：GTFS 公交时刻表需在 ODPT 注册申请，架构留了口子没接——目前是真实步行/车程，换乘查询返回"未覆盖"降级
- **进程内状态**：EventBus backlog、OTP 缓存、tracer 都在内存，重启即丢——单实例部署够用，水平扩展要换 Redis 之类

## 当前状态与已知缺口

代码能跑、测试全绿，离"可上线"还有一批已知缺口（2026-08-26 逐项核实，详见 ANALYSIS.md §6）：Docker 镜像缺 data/、nginx SSE 缓冲、LLM 故障兜底漏洞、OTP 无熔断等。修复按 A（跑通）→ B（故障兜底）→ C（体验）→ D（长跑健康）→ E（anitabi 健壮性）推进，A1-A3 已完成。
