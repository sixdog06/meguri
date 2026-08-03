"""Tracing 钩子约定（骨架）。

Orchestrator 在循环关键点调用 tracer.record(name, payload)：
  loop_step —— 每轮 ReAct 迭代开始
  llm_call  —— 每次调用 LLM 网关
  tool_call —— 每次执行工具（含观察结果）
评测/调试时通过 FastAPI dependency override 换成自己的 Tracer 来检查事件流。
本票刻意不做 span/exporter（后续 ticket 再接 OpenTelemetry 等）。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass(frozen=True)
class TraceEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Tracer(Protocol):
    def record(self, name: str, payload: dict[str, Any]) -> None: ...


class InMemoryTracer:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def record(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append(TraceEvent(name=name, payload=payload))
