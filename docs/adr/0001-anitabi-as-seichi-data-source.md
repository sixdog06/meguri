# 以 anitabi.cn 公开 API 作为圣地数据源

圣地数据（作品、取景地坐标、对照截图）直接调用 anitabi.cn 的公开 API（`https://api.anitabi.cn/`），一期不自建圣地数据库。

关键约束：该 API **仅允许非商业、非盈利项目使用**。本项目定位为个人开源项目/作品集，当前兼容；若未来有任何商业化可能，必须先把数据源抽象层换成合规替代源（聖地巡礼マップ、动画旅游协会、自建库等），因此所有圣地数据访问必须走统一的 repository 接口，不得散落调用。

**Considered Options**：LLM 知识+实时搜索（幻觉与坐标精度风险，拒）；自建精选库（冷启动太慢，可作为长期补充，一期拒）。

**补充（数据层架构，用户拍板最终版，以此为准）**：**本地 JSON 唯一职责是 ID↔名字映射**（`python -m app.ingest_bangumi` 拉 1990 年后全部动画存 `data/works/anime-1990plus.json`）。运行流程：作品名 → 本地映射 → subjectID → **实时**调 anitabi `/lite` 拿圣地数据。anitabi 调用失败（权限/网络/403/超时/非 JSON）→ 抛 `SeichiSourceUnavailable` 显式 503，**不降级本地数据包**；anitabi 成功但无数据 → 显式告知"这部作品没有圣地巡礼数据"（区别于故障）。早期"bgm.tv 实时解析"与"实时失败降级本地数据包（FallbackSeichiRepository）"两个补充均作废。
