# v1 直接上 OpenTripPlanner 做交通规划

Navigator 的交通数据不自欺：v1 即引入 OpenTripPlanner（OTP）+ 日本 GTFS 开放数据（公共交通オープンデータセンター / TokyoGTFS）+ OSM，提供真实的换乘路线、耗时与票价；不做"OSRM+LLM 估算"的过渡版。

**为什么**：用户选择一步到位——交通耗时与票价是行程可执行性和预算服务的根基，估算版会把不确定性传染给 Planner 和预算两个下游。

**Consequences**：部署被锁定为 Docker（OTP 是吃数 GB 内存的 JVM 服务，serverless 放不下）；需要按目标区域构建 routing graph 的 pipeline；GTFS 覆盖度取决于当地运营商（京都/东京圈好，偏远地区可能缺数据，需降级策略）。MVP 收敛为单城市，部分原因就是为了把 graph 构建范围控住。

**Considered Options**：OSRM+LLM 估算 v1、OTP v2 分期（链路验证更快，但早晚要做，且两版交通语义不一致，用户已拒）；Google Directions API（数据最全，但按量付费且国内部署受限，拒）。

**补充（#6）**：目标城市定为宇治/京都。OSM 用 openstreetmap.fr 的 kansai extract 经 osmium 裁剪（pipeline：`otp/download.sh` + `otp/build.sh`）。GTFS 实际情况：京都市営バス/地下鉄的 GTFS-JP 只在 ODPT（公共交通オープンデータセンター）发布且需免费注册 consumerKey，宇治的 JR 奈良线/京阪宇治线无公开 GTFS——pipeline 支持把 GTFS zip drop-in 进 `otp/data/` 重建；无 GTFS 期间 graph 只含路网，Navigator 对未覆盖查询返回 degraded 降级段（验收要求的"明确降级提示"）。开放时间校验落地为 OSM opening_hours（Overpass API）+ 计划时刻推算。
