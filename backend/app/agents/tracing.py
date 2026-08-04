"""Tracing 钩子约定（骨架）。

Orchestrator 在循环关键点调用 tracer.record(name, payload)：
  loop_step —— 每轮 ReAct 迭代开始
  llm_call  —— 每次调用 LLM 网关
  tool_call —— 每次执行工具（含观察结果）
  pipeline_stage —— 工具内部管线各阶段（检索/聚类/排序/校验/讲解，#10 补）
评测/调试时通过 FastAPI dependency override 换成自己的 Tracer 来检查事件流；
JsonlTracer 可把事件导出为 JSONL（#10）。本票刻意不做 span/exporter
（后续 ticket 再接 OpenTelemetry 等）。
"""

import json
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


class JsonlTracer:
    """把 trace 事件导出为 JSONL 结构化记录（#10 评测消费用）。

    每行一个事件：{"name", "payload", "timestamp"}。刻意保持小，不建 span 体系。
    用法：FastAPI dependency override get_tracer → JsonlTracer("trace.jsonl")。
    """

    def __init__(self, path: str) -> None:
        self._path = path

    def record(self, name: str, payload: dict[str, Any]) -> None:
        event = TraceEvent(name=name, payload=payload)
        line = json.dumps(
            {
                "name": event.name,
                "payload": event.payload,
                "timestamp": event.timestamp.isoformat(),
            },
            ensure_ascii=False,
            default=str,
        )
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
