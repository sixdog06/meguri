"""Orchestrator：自研的最小 ReAct Agent Loop（ADR-0002，不引入 Agent 框架）。

循环：收用户消息 → 落库 → 逐轮把对话历史交给 LLMGateway：
  - LLM 返回最终回复 → 落库、结束
  - LLM 返回工具调用 → 执行工具，把观察结果（observation）追加进消息，继续下一轮
由 max_iterations 兜底，防止死循环。

LLM 网关的 wire format（约定，见 _parse_llm_output）：
网关返回 JSON 字符串，二选一：
  {"type": "final", "content": "..."}
  {"type": "tool_call", "name": "<tool name>", "args": {...}}
非 JSON 输出按最终回复原文处理（兼容简单 fake / 纯文本模型）。
"""

import json
import uuid
from dataclasses import asdict, is_dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.ports import LLMGateway
from app.agents.events import EventBus, event_bus
from app.agents.tools import ToolRegistry
from app.agents.tracing import InMemoryTracer, Tracer
from app.models import Conversation, Itinerary, Message


class ConversationNotFound(Exception):
    pass


def _parse_llm_output(raw: str) -> dict[str, Any]:
    """解析网关输出为 {"type": "final" | "tool_call", ...}；非 JSON 视为 final 原文。"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"type": "final", "content": raw}
    if isinstance(data, dict) and data.get("type") in ("final", "tool_call"):
        return data
    return {"type": "final", "content": raw}


class Orchestrator:
    def __init__(
        self,
        llm: LLMGateway,
        tools: ToolRegistry | None = None,
        tracer: Tracer | None = None,
        bus: EventBus | None = None,
        max_iterations: int = 5,
    ) -> None:
        self._llm = llm
        self._tools = tools if tools is not None else ToolRegistry()
        self._tracer = tracer if tracer is not None else InMemoryTracer()
        self._bus = bus if bus is not None else event_bus
        self._max_iterations = max_iterations

    def reply(self, session: Session, conversation_id: uuid.UUID, text: str) -> Message:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise ConversationNotFound(str(conversation_id))
        conversation_key = str(conversation_id)

        self._bus.publish(conversation_key, "received", {"text": text})

        user_message = Message(conversation_id=conversation.id, role="user", content=text)
        session.add(user_message)
        session.commit()

        messages = [
            {"role": message.role, "content": message.content}
            for message in conversation.messages
        ]

        # 注入进度回调（约定通道，见 Tool 协议）：支持进度上报的工具经此
        # 把各阶段进度发布到 SSE，同时写 trace（pipeline_stage，评测消费）
        for tool in self._tools.list():
            if hasattr(tool, "progress_sink"):

                def make_sink(tool_name: str):
                    def sink(stage: str) -> None:
                        self._bus.publish(conversation_key, "planning", {"stage": stage})
                        self._tracer.record(
                            "pipeline_stage", {"tool": tool_name, "stage": stage}
                        )

                    return sink

                tool.progress_sink = make_sink(tool.name)

        reply_text = ""
        # 工具经 structured 约定通道（见 Tool 协议）产出的结构化结果，
        # 按工具名收集进消息 payload / API 响应
        tool_outputs: dict[str, Any] = {}
        plan_attempted = False  # 本轮是否调用过规划工具（含失败），用于快照语义
        for step in range(1, self._max_iterations + 1):
            self._tracer.record("loop_step", {"step": step})
            self._bus.publish(conversation_key, "thinking", {"step": step})

            self._tracer.record("llm_call", {"step": step})
            raw = self._llm.complete(messages)
            action = _parse_llm_output(raw)

            if action["type"] == "tool_call":
                name = str(action.get("name", ""))
                args = action.get("args") or {}
                tool = self._tools.get(name)
                if tool is None:
                    observation = f"工具 {name} 不存在"
                else:
                    observation = tool.run(args)
                    if tool.name == "plan_itinerary":
                        plan_attempted = True
                    structured = getattr(tool, "structured", None)
                    if structured:
                        if isinstance(structured, dict):
                            tool_outputs[tool.name] = structured
                        else:
                            tool_outputs.setdefault(tool.name, []).extend(
                                asdict(item) if is_dataclass(item) else item
                                for item in structured
                            )
                self._tracer.record(
                    "tool_call",
                    {"step": step, "name": name, "args": args, "observation": observation},
                )
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "tool", "content": observation})
                continue

            reply_text = str(action.get("content", ""))
            break
        else:
            # 达到最大迭代仍未给出 final：用最后一轮原始输出兜底
            reply_text = raw

        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=reply_text,
            payload=tool_outputs or None,
        )
        session.add(assistant_message)

        # 行程快照持久化：本轮生成了行程则落一份快照文档（#5）；
        # 规划被调用但失败（如无候选圣地）时落空快照占位——“最新快照”
        # 语义保持一致，旧快照不会在 GET /itinerary 里复活
        itinerary_payload = tool_outputs.get("plan_itinerary")
        if itinerary_payload:
            session.add(Itinerary(conversation_id=conversation.id, payload=itinerary_payload))
        elif plan_attempted:
            session.add(Itinerary(conversation_id=conversation.id, payload={}))
        session.commit()

        self._bus.publish(conversation_key, "done", {"reply": reply_text})
        return assistant_message
