"""Planner（#5）：地理聚类 + 日程切分 + 天内最近邻排序，产出结构化行程快照。

纯确定性模块（ADR-0002 自研编排的一部分，不引入框架），不依赖外部服务：
- 聚类：确定性 k-means 简化版（最远点采样初始化 + 少量 Lloyd 迭代，haversine 距离），
  空簇经最大簇对半拆分补齐，保证 day_count == min(请求天数, 候选圣地数)
- 天内顺序：从最北点出发的最近邻排序；天序按簇均纬度自北往南
- 交通段：天内相邻点 + 每天末尾到次日开头的跨天段，均为 haversine 距离估算

产出 ItinerarySnapshot（dataclass 结构，与 ports.Seichi 风格一致）；
序列化（asdict）发生在工具/持久化边界。
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from app.adapters.ports import Seichi
from app.geo import haversine_km

if TYPE_CHECKING:
    from app.agents.budget import BudgetReport  # 避免循环导入（budget 依赖本模块）
    from app.agents.storyteller import Narration  # 类型定义在 storyteller，本模块只引用

WALK_MAX_KM = 2.0  # 超过此距离按车程估算
WALK_KMH = 5.0
DRIVE_KMH = 30.0
_KMEANS_ITERATIONS = 10

Progress = Callable[[str], None]


@dataclass
class TransitLeg:
    """交通段（点与点、天与天之间的衔接）。

    本 schema（mode / duration_minutes / fare_yen / estimate）即 #6 OTP 的
    数据契约——#6 只换数据源（haversine 距离估算 → OTP 真实查询），不动 schema。
    degraded/note 为降级提示字段：OTP 不可达或区域未覆盖时保留估算段并标记。
    """

    from_id: str
    to_id: str
    mode: str  # walk / drive / transit（OTP 真实查询后为 walk / transit）
    distance_km: float
    duration_minutes: int
    estimate: bool = True
    fare_yen: int | None = None  # 日本 GTFS 常缺票价数据，拿不到保持 None
    cross_day: bool = False  # True = 每天末尾到次日开头的连接段
    degraded: bool = False  # True = 交通查询失败/未覆盖，已保留估算（明确降级提示）
    note: str | None = None


@dataclass
class StopCheck:
    """单站时间校验结果（#6）：计划到达时间 + 开放时间校验。"""

    seichi_id: str
    arrive_time: str  # "HH:MM" 计划到达
    open: bool | None = None  # None = 开放时间未知（不误标）
    note: str | None = None


@dataclass
class ItineraryDay:
    day: int
    seichi: list[Seichi]
    legs: list[TransitLeg] = field(default_factory=list)
    checks: list[StopCheck] = field(default_factory=list)
    narrations: list["Narration"] = field(default_factory=list)


@dataclass
class ItinerarySnapshot:
    """行程快照（CONTEXT.md：行程 Itinerary）：按天组织的圣地序列 + 交通段。"""

    day_count: int
    days: list[ItineraryDay]
    work: str | None = None
    area: str | None = None
    budget: "BudgetReport | None" = None  # 预算报告（#7 预算服务汇总；asdict 推迟到持久化边界）


def _distance(a: Seichi, b: Seichi) -> float:
    return haversine_km(a.lat, a.lng, b.lat, b.lng)


def _cluster(seichi: list[Seichi], k: int) -> list[list[Seichi]]:
    """确定性 k-means 简化版：最远点采样取 k 个初始质心，Lloyd 迭代收敛。"""
    if len(seichi) <= k:
        return [[s] for s in seichi]
    # 最远点采样：从最北点出发，逐个加入离已有种子最远的点
    seeds = [max(seichi, key=lambda s: s.lat)]
    while len(seeds) < k:
        seeds.append(max(seichi, key=lambda s: min(_distance(s, c) for c in seeds)))
    centroids = [(c.lat, c.lng) for c in seeds]
    clusters: list[list[Seichi]] = [[] for _ in range(k)]
    for _ in range(_KMEANS_ITERATIONS):
        clusters = [[] for _ in range(k)]
        for s in seichi:
            i = min(range(k), key=lambda i: haversine_km(s.lat, s.lng, *centroids[i]))
            clusters[i].append(s)
        centroids = [
            (
                sum(s.lat for s in cluster) / len(cluster),
                sum(s.lng for s in cluster) / len(cluster),
            )
            if cluster
            else centroids[i]
            for i, cluster in enumerate(clusters)
        ]
    return [cluster for cluster in clusters if cluster]


def _ensure_cluster_count(clusters: list[list[Seichi]], target: int) -> list[list[Seichi]]:
    """空簇补齐：反复把最大簇按经度对半拆分，直到簇数达到 target。

    （同坐标点会让 k-means 种子重合、产生空簇；target <= 总点数时必然可补齐。）
    """
    clusters = [c for c in clusters if c]
    while len(clusters) < target:
        largest = max(clusters, key=len)
        clusters.remove(largest)
        ordered = sorted(largest, key=lambda s: (s.lng, s.lat))
        mid = max(1, len(ordered) // 2)
        clusters.extend([ordered[:mid], ordered[mid:]])
    return clusters


def _order_nearest_neighbor(cluster: list[Seichi]) -> list[Seichi]:
    """天内顺序：从最北点出发的最近邻排序。"""
    remaining = list(cluster)
    ordered = [max(remaining, key=lambda s: s.lat)]
    remaining.remove(ordered[0])
    while remaining:
        nxt = min(remaining, key=lambda s: _distance(ordered[-1], s))
        ordered.append(nxt)
        remaining.remove(nxt)
    return ordered


def _estimate_leg(a: Seichi, b: Seichi, *, cross_day: bool = False) -> TransitLeg:
    distance = _distance(a, b)
    mode = "walk" if distance <= WALK_MAX_KM else "drive"
    speed = WALK_KMH if mode == "walk" else DRIVE_KMH
    return TransitLeg(
        from_id=str(a.id),
        to_id=str(b.id),
        mode=mode,
        distance_km=round(distance, 2),
        duration_minutes=max(1, round(distance / speed * 60)),
        cross_day=cross_day,
    )


def plan_itinerary(
    seichi: list[Seichi], days: int, progress: Progress | None = None
) -> ItinerarySnapshot:
    """对候选圣地做地理聚类与日程切分，产出按天组织的行程快照。"""
    emit = progress or (lambda stage: None)
    days = max(1, days)

    # 没有 id 的圣地给快照内序号，交通段以 id 引用（避免重名断链）
    normalized: list[Seichi] = []
    seq = 0
    for s in seichi:
        if s.id is None:
            seq += 1
            s = replace(s, id=f"seq-{seq}")
        normalized.append(s)

    emit("聚类中")
    target = min(days, len(normalized))
    clusters = _ensure_cluster_count(_cluster(normalized, target), target)
    # 日程从北往南排（确定性），保证同一天号的簇稳定
    clusters.sort(key=lambda c: -sum(s.lat for s in c) / len(c))

    emit("排序中")
    day_list: list[ItineraryDay] = []
    for i, cluster in enumerate(clusters, start=1):
        ordered = _order_nearest_neighbor(cluster)
        day_list.append(
            ItineraryDay(
                day=i,
                seichi=ordered,
                legs=[_estimate_leg(a, b) for a, b in zip(ordered, ordered[1:])],
            )
        )
    # 跨天连接段：每天末尾 → 次日开头，挂在出发天的 legs 末尾
    for current, nxt in zip(day_list, day_list[1:]):
        current.legs.append(_estimate_leg(current.seichi[-1], nxt.seichi[0], cross_day=True))

    return ItinerarySnapshot(day_count=len(day_list), days=day_list)
