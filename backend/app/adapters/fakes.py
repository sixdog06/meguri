"""Deterministic fakes for the adapter ports.

Used by tests (wired at the HTTP seam via dependency overrides) and by local
development while live adapters don't exist yet.
"""

import json
import re
from typing import Any

from app.adapters.ports import Seichi

# 开发期演示启发式（接真实 LLM 后删除）：识别少量作品/地区/天数关键词，直接
# 编排 search_seichi / plan_itinerary 工具调用——让页面在 fake LLM 下也能
# 演示真实检索与规划链路。仅在没有 scripted 脚本时生效，不影响测试。
_WORK_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("京吹", "吹响吧！上低音号"),
    ("上低音号", "吹响吧！上低音号"),
    ("ユーフォニアム", "吹响吧！上低音号"),
    ("轻音", "轻音少女"),
    ("K-ON", "轻音少女"),
)
_AREA_KEYWORDS: tuple[tuple[str, str], ...] = (("宇治", "宇治"),)
_CN_DIGITS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}
_DAYS_PATTERN = re.compile(r"([0-9]+|[一二两三四五六七])\s*天")


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
        """演示启发式：工具观察 → 汇总 final；命中关键词 → 检索/规划工具调用。"""
        last = messages[-1] if messages else {}
        if last.get("role") == "tool":
            return self._summarize_observation(last.get("content") or "")
        text = last.get("content") or ""
        work = next((w for kw, w in _WORK_KEYWORDS if kw in text), None)
        if not work:
            return "fake-llm-response"
        area = next((a for kw, a in _AREA_KEYWORDS if kw in text), "")
        days_match = _DAYS_PATTERN.search(text)
        if days_match:
            token = days_match.group(1)
            days = int(token) if token.isdigit() else _CN_DIGITS[token]
            return json.dumps(
                {
                    "type": "tool_call",
                    "name": "plan_itinerary",
                    "args": {"work": work, "area": area, "days": days},
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"type": "tool_call", "name": "search_seichi", "args": {"work": work, "area": area}},
            ensure_ascii=False,
        )

    @staticmethod
    def _summarize_observation(content: str) -> str:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return content or "fake-llm-response"  # 纯文本观察（如“没有找到…”）原样回复
        if isinstance(data, dict) and "days" in data:  # plan_itinerary 的行程快照
            total = sum(len(day["seichi"]) for day in data["days"])
            return f"已为你规划 {data['day_count']} 天行程，共 {total} 个圣地，详见行程与地图。"
        if isinstance(data, list) and data:  # search_seichi 的候选列表
            return f"为你找到 {len(data)} 个候选圣地，已在地图上标注。"
        return "没有找到符合条件的圣地。"


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
