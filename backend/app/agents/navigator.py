"""Navigator（#6）：交通与时间校验（CONTEXT.md：交通与时间 Agent）。

纯确定性模块，对 Planner 产出的 ItinerarySnapshot 做三件事：
1. 交通段真实化：经 TransitClient 端口（live = OTP）逐段查询，
   estimate=False 的真实结果替换 Planner 的距离估算（leg schema 不动，只换数据源）；
   查询失败/区域未覆盖 → 保留估算段 + degraded=True + note（明确降级提示，
   不报错不沉默）；fake 的 estimate=True 结果 = 没有真实数据，静默保留估算。
2. 天内顺序优化（optimize_day_orders，仅初始规划流程调用）：有真实交通
   数据源时按耗时矩阵（duration_matrix，可选端口方法）重排天内站点，
   替代直线距离最近邻——编辑流程不调用，用户手动顺序优先。
3. 时刻推算：每天 09:00 出发、每站停留 VISIT_MINUTES，推算各站计划到达时间。
"""

from datetime import date, datetime, time, timedelta
from typing import Any

from app.adapters.ports import Seichi, TransitClient
from app.agents.planner import (
    ItinerarySnapshot,
    Progress,
    StopCheck,
    TransitLeg,
    estimate_leg,
    order_path,
    rebuild_days,
)

DAY_START = time(9, 0)
VISIT_MINUTES = 45
# 耗时矩阵查询的固定出发时刻：只要"白天有车、大概多久"的代表性耗时，
# 不对齐具体班次（时刻表级优化超出本站需求）。
MATRIX_DEPART = time(10, 0)


def _leg_endpoint(
    seichi_by_id: dict[str, Any], leg: TransitLeg, end: str
) -> tuple[float, float]:
    s = seichi_by_id[leg.from_id if end == "from" else leg.to_id]
    return (s.lat, s.lng)


def validate_itinerary(
    snapshot: ItinerarySnapshot,
    transit: TransitClient | None = None,
    *,
    day_date: date | None = None,
    progress: Progress | None = None,
) -> ItinerarySnapshot:
    """就地更新 snapshot 的 legs 与 checks，返回 snapshot。"""
    emit = progress or (lambda stage: None)
    at_date = day_date or date.today()
    emit("校验中")
    seichi_by_id = {str(s.id): s for d in snapshot.days for s in d.seichi}

    for day in snapshot.days:
        clock = datetime.combine(at_date, DAY_START)
        intra_legs = [leg for leg in day.legs if not leg.cross_day]
        for i, stop in enumerate(day.seichi):
            # --- 时刻推算：记录计划到达时间 ---
            day.checks.append(
                StopCheck(
                    seichi_id=str(stop.id),
                    arrive_time=clock.strftime("%H:%M"),
                )
            )
            # --- 交通段真实化 + 时刻推进 ---
            depart = clock + timedelta(minutes=VISIT_MINUTES)
            if i < len(intra_legs):
                leg = intra_legs[i]
                _resolve_leg(leg, seichi_by_id, transit, depart)
                clock = depart + timedelta(minutes=leg.duration_minutes)
        # 跨天连接段：当天最后一站参观结束后出发（不计入当日后续时刻）
        cross = [leg for leg in day.legs if leg.cross_day]
        if cross:
            _resolve_leg(cross[0], seichi_by_id, transit, clock + timedelta(minutes=VISIT_MINUTES))
    return snapshot


def optimize_day_orders(
    snapshot: ItinerarySnapshot,
    transit: TransitClient | None,
    *,
    day_date: date | None = None,
    progress: Progress | None = None,
) -> ItinerarySnapshot:
    """天内顺序优化（仅初始规划流程调用；编辑流程保留用户手动顺序，不调用本函数）。

    经 TransitClient.duration_matrix（可选端口方法，无此方法的 fake → 整体跳过）
    拿真实公交+步行耗时矩阵，替代直线
    距离做多起点最近邻 + 2-opt 重排；矩阵缺项回退距离估算，整天矩阵 None
    （全部查询失败）保持原顺序。重排后重建交通段（估算段，由
    validate_itinerary 随后替换为真实段）。
    """
    if transit is None:
        return snapshot
    matrix_fn = getattr(transit, "duration_matrix", None)
    if matrix_fn is None:
        return snapshot
    emit = progress or (lambda stage: None)
    at = datetime.combine(day_date or date.today(), MATRIX_DEPART)
    for day in snapshot.days:
        if len(day.seichi) <= 2:
            continue
        stops = day.seichi
        matrix = matrix_fn([(s.lat, s.lng) for s in stops], depart_at=at)
        if matrix is None:
            continue
        emit("优化中")
        index = {id(s): i for i, s in enumerate(stops)}

        def cost(a: Seichi, b: Seichi) -> float:
            m = matrix[index[id(a)]][index[id(b)]]
            # 矩阵缺项（查询失败/无真实数据）回退直线距离估算
            return m if m is not None else estimate_leg(a, b).duration_minutes

        day.seichi = order_path(stops, cost)
    # 重排可能改变了天末/天首站点，跨天衔接段一并重建
    snapshot.days = rebuild_days(snapshot.days)
    return snapshot


def _resolve_leg(
    leg: TransitLeg,
    seichi_by_id: dict[str, Any],
    transit: TransitClient | None,
    depart: datetime,
) -> None:
    if transit is None:
        return
    try:
        result = transit.route(
            _leg_endpoint(seichi_by_id, leg, "from"),
            _leg_endpoint(seichi_by_id, leg, "to"),
            depart_at=depart,
        )
    except Exception as exc:  # OTP 不可达 / 区域未覆盖（NoRouteError）等
        leg.degraded = True
        leg.note = f"交通查询失败（{exc.__class__.__name__}），已保留距离估算"
        return
    if result.get("estimate"):
        return  # 没有真实数据（fake）→ 静默保留估算
    leg.mode = str(result["mode"])
    leg.duration_minutes = int(result["duration_minutes"])
    leg.fare_yen = result.get("fare_yen")
    leg.estimate = False
    leg.degraded = bool(result.get("degraded", False))  # 如 GTFS 未覆盖的降级提示
    leg.note = result.get("note")
