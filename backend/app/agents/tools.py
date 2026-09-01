"""工具系统骨架（ADR-0002 自研编排的一部分）。

Tool = Agent Loop 可调用的能力单元；ToolRegistry = 按名查找的注册表。
"""

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Protocol

from app.adapters.anitabi import NoSeichiData
from app.adapters.ports import (
    CorpusStore,
    LLMGateway,
    Seichi,
    SeichiRepository,
    TransitClient,
)
from app.agents.planner import plan_itinerary
from app.agents.navigator import optimize_day_orders
from app.agents.revalidate import finalize_snapshot


class Tool(Protocol):
    name: str
    description: str
    #: system prompt 动态工具清单用的参数说明（可选；Orchestrator 读取，
    #: 缺省为空串）。如 '{"ani_name": "作品名", "days": 天数整数}'
    args_hint: str
    #: 结构化输出通道（约定）：工具把最近一次 run 的结构化结果放在这里，
    #: Orchestrator 按工具名收集进消息 payload；无结构化输出的工具保持 None。
    structured: Any
    #: 用户可见提示通道（约定）：非错误的显式业务结果（如"该作品没有圣地
    #: 巡礼数据"）放这里，Orchestrator 收进 payload["notice"] 随响应返回。
    notice: str | None
    #: 进度回调（约定）：支持进度上报的工具暴露该属性，Orchestrator 在每次
    #: 回复前注入（发布 planning 事件到 SSE）；不暴露该属性的工具不上报。
    progress_sink: Callable[[str], None] | None

    def run(self, args: dict[str, Any]) -> str:
        """执行工具：args 为线格式里的 JSON 参数；返回给 LLM 的观察值文本。"""
        ...


class SearchSeichiTool:
    """Scout 的圣地检索工具（#4）：按作品+地区经 SeichiRepository 端口检索。

    run() 返回给 LLM 的观察值（observation）是 JSON 文本；结构化结果同时
    留在 structured 属性上，由 Orchestrator 按工具名收集进消息 payload /
    API 响应，不只混在文本里。
    """

    name = "search_seichi"
    description = "按作品+地区检索候选圣地（名称、坐标、对照截图、出处集数）"
    # system prompt 动态工具清单用（Orchestrator._system_prompt）
    args_hint = '{"ani_name": "作品中文全名", "area": "城市/地区名"}'

    def __init__(self, repository: SeichiRepository) -> None:
        self._repository = repository
        self.structured: list[Seichi] | None = None
        self.notice: str | None = None

    def run(self, args: dict[str, Any]) -> str:
        """检索候选圣地：观察值是候选 JSON；空结果分三种如实区分——
        未收录（普通空）、无巡礼数据（NoSeichiData，显式提示）、
        数据源故障（SeichiSourceUnavailable 上抛，API 映射 503）。
        """
        work = str(args.get("ani_name") or "").strip()
        area = str(args.get("area") or "").strip()
        self.notice = None
        try:
            self.structured = self._repository.search_seichi(work, area)
        except NoSeichiData as exc:
            self.structured = []
            self.notice = str(exc)
            return f"《{work}》没有圣地巡礼数据（不是检索失败，是该作品在 anitabi 无记录）"
        # 离线兜底提示（约定通道）：live 源故障降级到本地数据包时如实告知用户
        fallback = getattr(self._repository, "fallback_notice", None)
        if fallback:
            self.notice = fallback
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
    # system prompt 动态工具清单用（Orchestrator._system_prompt）
    args_hint = '{"ani_name": "作品中文全名", "area": "城市/地区名", "days": 天数整数}'

    MAX_DAYS = 7

    def __init__(
        self,
        repository: SeichiRepository,
        transit: TransitClient | None = None,
        corpus: CorpusStore | None = None,
        llm: LLMGateway | None = None,
    ) -> None:
        self._repository = repository
        self._transit = transit
        self._corpus = corpus
        self._llm = llm  # 提供时 Storyteller 走生成式讲解（真实模型）
        self.structured: dict[str, Any] | None = None
        self.notice: str | None = None
        self.progress_sink: Callable[[str], None] | None = None

    def _progress(self, stage: str) -> None:
        """上报规划阶段（progress_sink 未注入时为空转）。"""
        if self.progress_sink is not None:
            self.progress_sink(stage)

    def run(self, args: dict[str, Any]) -> str:
        """生成行程快照：检索 → 聚类规划 → 校验/讲解收尾 → JSON 观察值。

        参数容忍模型给的脏值（days 非整数回退默认）。
        """
        work = str(args.get("ani_name") or "").strip()
        area = str(args.get("area") or "").strip()
        try:
            days = int(args.get("days") or 1)
        except (TypeError, ValueError):
            days = 1
        days = min(max(1, days), self.MAX_DAYS)

        self._progress("检索中")
        self.notice = None
        try:
            seichi = self._repository.search_seichi(work, area)
        except NoSeichiData as exc:
            self.notice = str(exc)
            self.structured = None
            return f"《{work}》没有圣地巡礼数据，无法规划行程（该作品在 anitabi 无记录，不是检索失败）"
        # 离线兜底提示（约定通道）：live 源故障降级到本地数据包时如实告知用户
        fallback = getattr(self._repository, "fallback_notice", None)
        if fallback:
            self.notice = fallback
        if not seichi:
            self.structured = None
            return "没有找到候选圣地，无法规划行程"

        snapshot = plan_itinerary(seichi, days, progress=self._progress)
        snapshot.work = work
        snapshot.area = area
        # 天内顺序优化（可选）：有真实交通数据源时按耗时矩阵重排，替代几何最近邻；
        # 只在初始规划做——编辑流程保留用户手动顺序（见 revalidate_snapshot）
        if self._transit is not None:
            optimize_day_orders(snapshot, self._transit, progress=self._progress)
        # 收尾管线（revalidate.finalize_snapshot）：Navigator 校验 → 讲解
        finalize_snapshot(
            snapshot,
            transit=self._transit,
            corpus=self._corpus,
            progress=self._progress,
            llm=self._llm,
        )
        self._progress("完成")

        self.structured = asdict(snapshot)
        return json.dumps(self.structured, ensure_ascii=False)


class ToolRegistry:
    """按名查找的工具注册表；Orchestrator 经它解析 tool_call 与生成工具清单。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册工具（同名覆盖）。"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名取工具；不存在返回 None（编排层据此回“工具不存在”观察）。"""
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        """全部已注册工具（system prompt 工具清单、progress_sink 注入用）。"""
        return list(self._tools.values())
