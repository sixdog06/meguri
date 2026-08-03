"""Navigator（#6）：交通与时间校验（CONTEXT.md：交通与时间 Agent）。

纯确定性模块，对 Planner 产出的 ItinerarySnapshot 做三件事：
1. 交通段真实化：经 TransitClient 端口（live = OTP）逐段查询，
   estimate=False 的真实结果替换 Planner 的距离估算（leg schema 不动，只换数据源）；
   查询失败/区域未覆盖 → 保留估算段 + degraded=True + note（明确降级提示，
   不报错不沉默）；fake 的 estimate=True 结果 = 没有真实数据，静默保留估算。
2. 时刻推算：每天 09:00 出发、每站停留 VISIT_MINUTES，推算各站计划到达时间。
3. 开放时间校验：经 OpeningHoursSource（OSM opening_hours）判断到达时刻
   是否开放，闭馆 → StopCheck(open=False, note)；未知 → open=None 不误标。
"""

from datetime import date, datetime, time, timedelta
from typing import Any

from app.adapters.ports import OpeningHoursSource, TransitClient
from app.agents.opening_hours import is_open
from app.agents.planner import (
    ItinerarySnapshot,
    Progress,
    StopCheck,
    TransitLeg,
)

DAY_START = time(9, 0)
VISIT_MINUTES = 45


def _leg_endpoint(
    seichi_by_id: dict[str, Any], leg: TransitLeg, end: str
) -> tuple[float, float]:
    s = seichi_by_id[leg.from_id if end == "from" else leg.to_id]
    return (s.lat, s.lng)


def validate_itinerary(
    snapshot: ItinerarySnapshot,
    transit: TransitClient | None = None,
    hours: OpeningHoursSource | None = None,
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
            # --- 开放时间校验 ---
            open_state: bool | None = None
            note = None
            if hours is not None:
                oh = hours.opening_hours(stop.lat, stop.lng)
                if oh:
                    open_state = is_open(oh, clock.time(), clock.weekday())
                    if open_state is False:
                        note = f"到达时可能不在开放时间（{oh}）"
            day.checks.append(
                StopCheck(
                    seichi_id=str(stop.id),
                    arrive_time=clock.strftime("%H:%M"),
                    open=open_state,
                    note=note,
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
