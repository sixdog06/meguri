"""预算服务（#7，CONTEXT.md：Budget Service）。

非 Agent 的确定性模块——刻意不用 LLM，保证零幻觉：
汇总 ItinerarySnapshot 的交通票价（legs.fare_yen）与圣地门票，输出
总预算、分项明细与超支告警。

诚实姿态：步行段本来免费——fare_yen=None 时按确定的 ¥0 计入合计；
非步行段的 None 才是"票价数据缺失"的未计价项（日本 GTFS 常缺票价）——
未计价项不计入合计、不参与超支判断，但显式计数提示存在，不静默当 0。
门票作为可选输入（admission_yen: seichi_id → 日元）；大部分圣地免费，
但没有数据源时不编造价格。
"""

from dataclasses import dataclass, field

from app.agents.planner import ItinerarySnapshot


@dataclass
class BudgetItem:
    label: str
    amount_yen: int | None  # None = 未计价


def _leg_fare(leg) -> int | None:
    """步行段免费是确定事实（¥0）；非步行段的 None 是票价缺失（未计价）。"""
    if leg.fare_yen is not None:
        return leg.fare_yen
    return 0 if leg.mode == "walk" else None


@dataclass
class BudgetReport:
    limit_yen: int | None
    total_yen: int  # 已计价合计
    over_budget: bool
    alert: str | None
    transit: list[BudgetItem] = field(default_factory=list)
    admission: list[BudgetItem] = field(default_factory=list)
    unpriced_count: int = 0


def summarize_budget(
    snapshot: ItinerarySnapshot,
    limit_yen: int | None = None,
    admission_yen: dict[str, int] | None = None,
) -> BudgetReport:
    """汇总行程快照的交通费与门票，产出预算报告（纯函数）。"""
    names = {str(s.id): s.name for d in snapshot.days for s in d.seichi}
    admission_yen = admission_yen or {}

    transit = [
        BudgetItem(
            label=f"{names.get(leg.from_id, leg.from_id)}→{names.get(leg.to_id, leg.to_id)}",
            amount_yen=_leg_fare(leg),
        )
        for day in snapshot.days
        for leg in day.legs
    ]
    admission = [
        BudgetItem(label=s.name, amount_yen=admission_yen.get(str(s.id)))
        for day in snapshot.days
        for s in day.seichi
    ]

    items = transit + admission
    total = sum(i.amount_yen for i in items if i.amount_yen is not None)
    unpriced = sum(1 for i in items if i.amount_yen is None)

    over = limit_yen is not None and total > limit_yen
    alert = None
    if over:
        alert = f"超出预算 {total - limit_yen} 日元（已计价合计 {total} / 上限 {limit_yen}）"

    return BudgetReport(
        limit_yen=limit_yen,
        total_yen=total,
        over_budget=over,
        alert=alert,
        transit=transit,
        admission=admission,
        unpriced_count=unpriced,
    )
