# 以 anitabi.cn 公开 API 作为圣地数据源

圣地数据（作品、取景地坐标、对照截图）直接调用 anitabi.cn 的公开 API（`https://api.anitabi.cn/`），一期不自建圣地数据库。

关键约束：该 API **仅允许非商业、非盈利项目使用**。本项目定位为个人开源项目/作品集，当前兼容；若未来有任何商业化可能，必须先把数据源抽象层换成合规替代源（聖地巡礼マップ、动画旅游协会、自建库等），因此所有圣地数据访问必须走统一的 repository 接口，不得散落调用。

**Considered Options**：LLM 知识+实时搜索（幻觉与坐标精度风险，拒）；自建精选库（冷启动太慢，可作为长期补充，一期拒）。

**补充（#4）**：anitabi 公开 API 只有 `/bangumi/{subjectID}/lite` 与 `/bangumi/{subjectID}/points/detail`，没有按名搜索作品的端点；其作品 id 即 bangumi.tv subjectID，故作品名 → subjectID 的解析经 bangumi.tv 官方 API（`POST https://api.bgm.tv/v0/search/subjects`）完成。该解析同样封装在 SeichiRepository 实现内部，不外泄。
