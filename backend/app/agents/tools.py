"""工具系统骨架（ADR-0002 自研编排的一部分）。

Tool = Agent Loop 可调用的能力单元；ToolRegistry = 按名查找的注册表。
"""

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Protocol

from app.adapters.ports import (
    CorpusStore,
    OpeningHoursSource,
    Seichi,
    SeichiRepository,
    TransitClient,
)
from app.agents.budget import summarize_budget
from app.agents.navigator import validate_itinerary
from app.agents.planner import plan_itinerary
from app.agents.storyteller import narrate_itinerary


class Tool(Protocol):
    name: str
    description: str
    #: 结构化输出通道（约定）：工具把最近一次 run 的结构化结果放在这里，
    #: Orchestrator 按工具名收集进消息 payload；无结构化输出的工具保持 None。
    structured: Any
    #: 进度回调（约定）：支持进度上报的工具暴露该属性，Orchestrator 在每次
    #: 回复前注入（发布 planning 事件到 SSE）；不暴露该属性的工具不上报。
    progress_sink: Callable[[str], None] | None

    def run(self, args: dict[str, Any]) -> str: ...


class SearchSeichiTool:
    """Scout 的圣地检索工具（#4）：按作品+地区经 SeichiRepository 端口检索。

    run() 返回给 LLM 的观察值（observation）是 JSON 文本；结构化结果同时
    留在 structured 属性上，由 Orchestrator 按工具名收集进消息 payload /
    API 响应，不只混在文本里。
    """

    name = "search_seichi"
    description = "按作品+地区检索候选圣地（名称、坐标、对照截图、出处集数）"

    def __init__(self, repository: SeichiRepository) -> None:
        self._repository = repository
        self.structured: list[Seichi] | None = None

    def run(self, args: dict[str, Any]) -> str:
        work = str(args.get("work") or "").strip()
        area = str(args.get("area") or "").strip()
        self.structured = self._repository.search_seichi(work, area)
        if not self.structured:
            return "没有找到符合条件的圣地"
        return json.dumps([asdict(s) for s in self.structured], ensure_ascii=False)


class PlanItineraryTool:
    """Planner 的行程生成工具（#5）：检索候选圣地 → 聚类切分 → 行程快照。

    输入作品+地区+天数；候选集复用 SeichiRepository 端口再检索一次（与
    Scout 同一数据源，保持单一入口）。结构化行程快照留在 structured
    （plain dict，JSON 安全），由 Orchestrator 持久化并随响应返回。
    规划各阶段经 progress_sink 上报（检索中/聚类中/排序中/完成）。
    """

    name = "plan_itinerary"
    description = "按作品+地区+天数生成按天组织的行程快照（地理聚类、顺序优化、交通段估算）"

    MAX_DAYS = 7

    def __init__(
        self,
        repository: SeichiRepository,
        transit: TransitClient | None = None,
        hours: OpeningHoursSource | None = None,
        corpus: CorpusStore | None = None,
    ) -> None:
        self._repository = repository
        self._transit = transit
        self._hours = hours
        self._corpus = corpus
        self.structured: dict[str, Any] | None = None
        self.progress_sink: Callable[[str], None] | None = None

    def _progress(self, stage: str) -> None:
        if self.progress_sink is not None:
            self.progress_sink(stage)

    def run(self, args: dict[str, Any]) -> str:
        work = str(args.get("work") or "").strip()
        area = str(args.get("area") or "").strip()
        try:
            days = int(args.get("days") or 1)
        except (TypeError, ValueError):
            days = 1
        days = min(max(1, days), self.MAX_DAYS)
        try:
            budget_yen = int(args["budget_yen"]) if args.get("budget_yen") else None
        except (TypeError, ValueError):
            budget_yen = None

        self._progress("检索中")
        seichi = self._repository.search_seichi(work, area)
        if not seichi:
            self.structured = None
            return "没有找到候选圣地，无法规划行程"

        snapshot = plan_itinerary(seichi, days, progress=self._progress)
        snapshot.work = work
        snapshot.area = area
        # Navigator：真实交通段替换估算 + 开放时间与时刻校验（降级不报错）
        validate_itinerary(
            snapshot, self._transit, self._hours, progress=self._progress
        )
        # 预算服务（#7）：确定性汇总 + 超支告警，不经过 LLM
        snapshot.budget = summarize_budget(snapshot, limit_yen=budget_yen)
        # Storyteller（#8）：检索式讲解 + citation（语料为空则不产出，零幻觉）
        if self._corpus is not None:
            narrate_itinerary(snapshot, self._corpus, progress=self._progress)
        self._progress("完成")

        self.structured = asdict(snapshot)
        return json.dumps(self.structured, ensure_ascii=False)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())
