"""工具系统骨架（ADR-0002 自研编排的一部分）。

Tool = Agent Loop 可调用的能力单元；ToolRegistry = 按名查找的注册表。
"""

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Protocol

from app.adapters.anitabi import NoSeichiData
from app.adapters.ports import (
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
    #: 区域外摘要通道（约定）：检索/规划时被地区过滤整个滤掉的作品
    #: （[{work, city, count}]）放这里，Orchestrator 收进 payload["out_of_area"]；
    #: 无则保持 None。语义：显式排除并告知用户，不静默丢弃。
    out_of_area: list[dict] | None

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
        self.out_of_area: list[dict] | None = None

    def run(self, args: dict[str, Any]) -> str:
        """检索候选圣地：观察值是候选 JSON（多作品命中时按作品分组统计）；
        空结果分三种如实区分——未收录（普通空）、无巡礼数据（NoSeichiData，
        显式提示）、数据源故障（SeichiSourceUnavailable 上抛，API 映射 503）。
        被地区过滤整个滤掉的作品进 out_of_area 通道，并在观察值里提示
        模型告知用户。
        """
        work = str(args.get("ani_name") or "").strip()
        area = str(args.get("area") or "").strip()
        self.notice = None
        self.out_of_area = None
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
        # 区域外摘要（约定通道）：被地区过滤滤掉的作品要告知用户"以后可去"
        out_of_area = getattr(self._repository, "out_of_area", None) or []
        if out_of_area:
            self.out_of_area = out_of_area
        if not self.structured:
            if out_of_area:
                return (
                    "在指定地区没有找到符合条件的圣地。"
                    f"但该作品在这些地区有巡礼点：{_format_out_of_area(out_of_area)}。"
                    "请在回复中如实告知用户这些地点本次未包含、以后可以单独规划。"
                )
            return "没有找到符合条件的圣地"
        return json.dumps(
            _search_observation(self.structured, out_of_area), ensure_ascii=False
        )


def _format_out_of_area(out_of_area: list[dict]) -> str:
    """区域外摘要 → 一行人读文本（"《轻音少女 剧场版》欧洲 51 处"）。"""
    return "、".join(
        f"《{item['work']}》{item['city']} {item['count']} 处" for item in out_of_area
    )


def _search_observation(seichi: list[Seichi], out_of_area: list[dict]) -> dict:
    """检索观察值：候选明细 + 按作品分组统计 + 区域外摘要（多作品命中时
    模型据此如实分组告知；out_of_area 非空时必须转告用户）。"""
    by_work: dict[str, int] = {}
    for s in seichi:
        key = s.work or ""
        by_work[key] = by_work.get(key, 0) + 1
    observation: dict[str, Any] = {
        "candidates": [asdict(s) for s in seichi],
        "by_work": by_work,
    }
    if out_of_area:
        observation["out_of_area"] = out_of_area
        observation["note"] = (
            "out_of_area 里的作品在本次地区之外有巡礼点，请在回复中告知用户"
            "这些地点本次未包含、以后可以单独规划"
        )
    return observation


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
        llm: LLMGateway | None = None,
    ) -> None:
        self._repository = repository
        self._transit = transit
        self._llm = llm  # 提供时 Storyteller 走生成式讲解（真实模型）
        self.structured: dict[str, Any] | None = None
        self.notice: str | None = None
        self.out_of_area: list[dict] | None = None
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
        self.out_of_area = None
        try:
            # 多作品命中（"轻音少女" → 第一季+第二季+剧场版）时合并规划：
            # 区域内候选并入同一候选集统一聚类；每站保留自己的 work
            # （讲解按各自作品的元数据生成，不串味）
            seichi = self._repository.search_seichi(work, area)
        except NoSeichiData as exc:
            self.notice = str(exc)
            self.structured = None
            return f"《{work}》没有圣地巡礼数据，无法规划行程（该作品在 anitabi 无记录，不是检索失败）"
        # 离线兜底提示（约定通道）：live 源故障降级到本地数据包时如实告知用户
        fallback = getattr(self._repository, "fallback_notice", None)
        if fallback:
            self.notice = fallback
        # 区域外摘要（约定通道）：只进 out_of_area 通道（payload + 观察文本里
        # 附告知指令，由模型在回复正文里转告用户），不进 notice——
        # 用户拍板：这类提示在聊天流里看，不弹 toast
        out_of_area = getattr(self._repository, "out_of_area", None) or []
        if out_of_area:
            self.out_of_area = out_of_area
        if not seichi:
            self.structured = None
            if out_of_area:
                return (
                    "在指定地区没有找到候选圣地，无法规划行程。"
                    f"但该作品在这些地区有巡礼点：{_format_out_of_area(out_of_area)}。"
                    "请在回复中如实告知用户这些地点本次未包含、以后可以单独规划。"
                )
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
            progress=self._progress,
            llm=self._llm,
        )
        self._progress("完成")

        self.structured = asdict(snapshot)
        observation = self.structured
        if out_of_area:
            # 观察值附带区域外摘要（仅给模型看；结构化快照本身不含）
            observation = {
                **self.structured,
                "out_of_area": out_of_area,
                "note": "out_of_area 里的作品在本次地区之外有巡礼点，请在回复中"
                        "告知用户这些地点未包含在行程内、以后可以单独规划",
            }
        return json.dumps(observation, ensure_ascii=False)


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
