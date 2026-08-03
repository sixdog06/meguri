"""Deterministic fakes for the adapter ports.

Used by tests (wired at the HTTP seam via dependency overrides) and by local
development while live adapters don't exist yet.
"""

import json
from typing import Any

from app.adapters.ports import Seichi

# 开发期演示启发式（接真实 LLM 后删除）：识别少量作品/地区关键词，直接编排
# search_seichi 工具调用——让页面在 fake LLM 下也能演示真实 anitabi 检索链路。
# 仅在没有 scripted 脚本时生效，不影响测试。
_WORK_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("京吹", "吹响吧！上低音号"),
    ("上低音号", "吹响吧！上低音号"),
    ("ユーフォニアム", "吹响吧！上低音号"),
    ("轻音", "轻音少女"),
    ("K-ON", "轻音少女"),
)
_AREA_KEYWORDS: tuple[tuple[str, str], ...] = (("宇治", "宇治"),)


class FakeLLMGateway:
    def __init__(self, scripted: list[str] | None = None) -> None:
        self._scripted = list(scripted or [])
        self.calls: list[list[dict[str, str]]] = []  # 每次 complete 收到的消息，供测试断言

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if self._scripted:
            return self._scripted.pop(0)
        return self._heuristic(messages)

    def _heuristic(self, messages: list[dict[str, str]]) -> str:
        """演示启发式：工具观察 → 汇总 final；命中作品关键词 → search_seichi 工具调用。"""
        last = messages[-1] if messages else {}
        if last.get("role") == "tool":
            try:
                count = len(json.loads(last.get("content") or "[]"))
            except json.JSONDecodeError:
                count = 0
            if count:
                return f"为你找到 {count} 个候选圣地，已在地图上标注。"
            return "没有找到符合条件的圣地。"
        text = last.get("content") or ""
        work = next((w for kw, w in _WORK_KEYWORDS if kw in text), None)
        if work:
            area = next((a for kw, a in _AREA_KEYWORDS if kw in text), "")
            return json.dumps(
                {"type": "tool_call", "name": "search_seichi", "args": {"work": work, "area": area}},
                ensure_ascii=False,
            )
        return "fake-llm-response"


class FakeSeichiRepository:
    def __init__(self, seichi: list[Seichi] | None = None) -> None:
        self._seichi = list(seichi or [])
        self.calls: list[tuple[str, str]] = []  # 每次 search_seichi 的 (work, area)，供测试断言

    def search_seichi(self, work: str, area: str) -> list[Seichi]:
        self.calls.append((work, area))
        # 地区宽松匹配，与 live 实现（anitabi 城市名）语义一致
        return [
            s
            for s in self._seichi
            if s.work == work and (not area or area in (s.area or "") or (s.area or "") in area)
        ]


class FakeTransitClient:
    def route(self, origin: tuple[float, float], destination: tuple[float, float]) -> dict[str, Any]:
        return {"mode": "fake", "duration_minutes": 0, "fare_yen": 0, "estimate": True}
