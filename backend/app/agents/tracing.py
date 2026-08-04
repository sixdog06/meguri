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
    """单条 trace 事件：名称 + 负载 + UTC 时间戳（不可变，可安全传递/落盘）。"""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Tracer(Protocol):
    """Tracing 钩子端口：编排关键点经 record 上报事件（评测/调试消费）。"""

    def record(self, name: str, payload: dict[str, Any]) -> None:
        """记录一个事件（name 见模块头注释清单；payload 须可 JSON 序列化）。"""
        ...


class InMemoryTracer:
    """内存 Tracer：事件留在 events 列表，测试/评测断言事件流用。"""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def record(self, name: str, payload: dict[str, Any]) -> None:
        """追加事件到内存列表。"""
        self.events.append(TraceEvent(name=name, payload=payload))


class JsonlTracer:
    """把 trace 事件导出为 JSONL 结构化记录（#10 评测消费用）。

    每行一个事件：{"name", "payload", "timestamp"}。刻意保持小，不建 span 体系。
    用法：FastAPI dependency override get_tracer → JsonlTracer("trace.jsonl")。
    """

    def __init__(self, path: str) -> None:
        self._path = path

    def record(self, name: str, payload: dict[str, Any]) -> None:
        """追加一行 JSONL 到目标文件（每写即 flush 关文件，崩溃不丢已写行）。"""
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
