"""工具系统骨架（ADR-0002 自研编排的一部分）。

Tool = Agent Loop 可调用的能力单元；ToolRegistry = 按名查找的注册表。
"""

import json
from dataclasses import asdict
from typing import Any, Protocol

from app.adapters.ports import Seichi, SeichiRepository


class Tool(Protocol):
    name: str
    description: str
    #: 结构化输出通道（约定）：工具把最近一次 run 的结构化结果放在这里，
    #: Orchestrator 按工具名收集进消息 payload；无结构化输出的工具保持 None。
    structured: list[Any] | None

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


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())
