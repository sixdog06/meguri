"""重校验管线（#9）：规划后/编辑后共用的"校验 + 预算 + 讲解"收尾编排。

- finalize_snapshot：Navigator 校验 → 预算重算 → Storyteller 讲解
  （PlanItineraryTool 规划流程与编辑流程共用这一段）。
- revalidate_snapshot：编辑专用前置（rebuild_days 重建失效交通段、清旧
  checks、保留未受影响站的讲解）后接 finalize。
"""

from app.adapters.ports import CorpusStore, LLMGateway, OpeningHoursSource, TransitClient
from app.agents.budget import summarize_budget
from app.agents.navigator import validate_itinerary
from app.agents.planner import ItinerarySnapshot, Progress, rebuild_days
from app.agents.storyteller import Narration, narrate_itinerary


def finalize_snapshot(
    snapshot: ItinerarySnapshot,
    *,
    transit: TransitClient | None,
    hours: OpeningHoursSource | None,
    corpus: CorpusStore | None,
    limit_yen: int | None,
    progress: Progress | None = None,
    existing_narrations: dict[str, Narration] | None = None,
    llm: LLMGateway | None = None,
) -> ItinerarySnapshot:
    """收尾管线：Navigator 校验 → 预算重算 → Storyteller 讲解（如有语料库）。

    llm 提供时讲解走生成式（接真实模型）；否则保持检索式拼装。"""
    validate_itinerary(snapshot, transit, hours, progress=progress)
    snapshot.budget = summarize_budget(snapshot, limit_yen=limit_yen)
    if corpus is not None:
        narrate_itinerary(
            snapshot, corpus, progress=progress, existing=existing_narrations, llm=llm
        )
    return snapshot


def revalidate_snapshot(
    snapshot: ItinerarySnapshot,
    *,
    transit: TransitClient | None,
    hours: OpeningHoursSource | None,
    corpus: CorpusStore | None,
    limit_yen: int | None,
    llm: LLMGateway | None = None,
) -> ItinerarySnapshot:
    """编辑后的自动重校验（#9）：重建失效交通段（估算）→ 校验/预算/讲解收尾。

    失效交通段先落回估算段，由 Navigator 替换为真实段；替换不了按 #6
    降级语义标记。未受影响站的讲解按 seichi_id 保留，新加入的站检索补充。
    """
    snapshot.days = rebuild_days(snapshot.days)
    snapshot.day_count = len(snapshot.days)
    for day in snapshot.days:
        day.checks = []
    existing = {n.seichi_id: n for d in snapshot.days for n in d.narrations}
    for day in snapshot.days:
        day.narrations = []
    return finalize_snapshot(
        snapshot,
        transit=transit,
        hours=hours,
        corpus=corpus,
        limit_yen=limit_yen,
        existing_narrations=existing,
        llm=llm,
    )
