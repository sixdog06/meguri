"""工具系统骨架（ADR-0002 自研编排的一部分）。

Tool = Agent Loop 可调用的能力单元；ToolRegistry = 按名查找的注册表。
本票只立骨架：生产 wiring 里注册表为空，具体工具（圣地检索、路线规划等）
由后续 ticket 注册进来。
"""

from typing import Any, Protocol


class Tool(Protocol):
    name: str
    description: str

    def run(self, args: dict[str, Any]) -> str: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())
