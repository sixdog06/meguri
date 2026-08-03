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
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.ports import LLMGateway
from app.agents.events import EventBus, event_bus
from app.agents.tools import ToolRegistry
from app.agents.tracing import InMemoryTracer, Tracer
from app.models import Conversation, Message


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

        reply_text = ""
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
                observation = tool.run(args) if tool is not None else f"工具 {name} 不存在"
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
            conversation_id=conversation.id, role="assistant", content=reply_text
        )
        session.add(assistant_message)
        session.commit()

        self._bus.publish(conversation_key, "done", {"reply": reply_text})
        return assistant_message
