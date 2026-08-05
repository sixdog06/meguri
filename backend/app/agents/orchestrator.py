"""Orchestrator：自研的最小 ReAct Agent Loop（ADR-0002，不引入 Agent 框架）。

循环：收用户消息 → 落库 → 逐轮把对话历史（含 system prompt）交给 LLMGateway：
  - LLM 返回最终回复 → 落库、结束
  - LLM 返回工具调用 → 执行工具，把观察结果（observation）追加进消息，继续下一轮
由 max_iterations 兜底，防止死循环。

LLM 网关的 wire format（约定，见 _parse_llm_output / _system_prompt）：
- 工具调用：一行 JSON `{"type": "tool_call", "name": "<tool>", "args": {...}}`
- 最终回复：**纯文本/Markdown 正文**（不包 JSON）——这样网关流式回调的
  增量文本可以逐字推 SSE 上屏（JSON 包装没法边收边显示）。
兼容兜底：模型仍按旧格式输出 `{"type": "final", "content": ...}`（含未闭合/
截断的残次 JSON）时提取 content；其余非 JSON 一律按 final 原文。
"""

import json
import re
import uuid
from dataclasses import asdict, is_dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.llm import LLMUnavailableError
from app.adapters.ports import LLMGateway
from app.agents.events import EventBus, event_bus
from app.agents.tools import ToolRegistry
from app.agents.tracing import InMemoryTracer, Tracer
from app.models import Conversation, Itinerary, Message


class ConversationNotFound(Exception):
    """会话不存在（编排层语义；API 边界映射 404）。"""


def _extract_json(raw: str) -> str:
    """从真实模型输出里提取 JSON：去 markdown fence、取第一个 { 到最后一个 }。"""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def _extract_truncated_final(raw: str) -> str | None:
    """从未闭合/截断的 final JSON 里抢救 content（真实模型偶发，曾导致
    原始 JSON 整串上屏）；不像 final JSON 时返回 None。"""
    text = raw.strip()
    match = re.search(
        r'\{\s*"type"\s*:\s*"final"\s*,\s*"content"\s*:\s*"(.*)', text, re.DOTALL
    )
    if not match:
        return None
    inner = match.group(1).rstrip()
    # 去掉可能的收尾残片（闭合引号/花括号）
    if inner.endswith('"}'):
        inner = inner[:-2]
    elif inner.endswith('"'):
        inner = inner[:-1]
    try:  # 含 \n \" 等合法转义：借 JSON 解码（真实换行先转义防炸）
        return json.loads('"' + inner.replace("\n", "\\n") + '"')
    except json.JSONDecodeError:  # 转义本身也残了：手工解最常见的几种
        return inner.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def _parse_llm_output(raw: str) -> dict[str, Any]:
    """解析网关输出为 {"type": "final" | "tool_call", ...}；非 JSON 视为 final 原文。"""
    for candidate in (raw, _extract_json(raw)):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("type") in ("final", "tool_call"):
            return data
    truncated = _extract_truncated_final(raw)
    if truncated is not None:
        return {"type": "final", "content": truncated}
    return {"type": "final", "content": raw}


def _system_prompt(tools: ToolRegistry) -> str:
    """角色 + 动态工具清单 + 输出线格式（工具清单从 ToolRegistry 生成，不硬编码）。"""
    tool_docs = []
    for tool in tools.list():
        args_hint = getattr(tool, "args_hint", "")
        tool_docs.append(f"- {tool.name}：{tool.description}。参数：{args_hint}")
    tools_text = "\n".join(tool_docs) if tool_docs else "（无可用工具）"
    return (
        "你是 Meguri，一个动画圣地巡礼行程规划助手。用户用中文描述想巡礼的作品、"
        "目的地和出行条件，你帮他检索圣地并规划行程。\n\n"
        "你可以调用以下工具：\n"
        f"{tools_text}\n\n"
        "规则：\n"
        "1. 需要检索或规划时，只输出一行 JSON 工具调用，不要输出任何其他文字：\n"
        '   {"type": "tool_call", "name": "<工具名>", "args": {<参数>}}\n'
        "2. 工具结果会以 [工具观察结果] 形式给你。拿到结果后，直接输出给用户的\n"
        "   最终回复正文（Markdown 可用），**不要包 JSON、不要代码围栏**。\n"
        "3. 不需要工具时（澄清、闲聊、信息不足），同样直接输出回复正文。\n"
        "4. 天数等参数从用户话里推断；作品名用中文全名。\n"
        "5. 用户给出作品+地区且表达了出行/巡礼意图时，即使没说天数，也要\n"
        "   检索后调用 plan_itinerary 出行程（天数按 2 天起合理推断，并在\n"
        "   回复里说明这个假设、告诉用户可以调整）；只有作品或地区缺失时\n"
        "   才先追问，不要只检索不规划。"
    )


class Orchestrator:
    """主对话 Agent（CONTEXT.md）：驱动 ReAct 循环、落库消息、分发进度事件。

    依赖全部经构造注入（LLM 网关/工具注册表/tracer/事件总线），
    测试在 HTTP 缝经 dependency override 替换。
    """

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
        """处理一条用户消息：跑 ReAct 循环，返回落库后的 assistant 消息。

        不变量：用户消息先落库；LLM 不可达（LLMUnavailableError）时推 error
        事件并上抛，assistant 侧不留脏数据；工具结构化产出按工具名进 payload。
        """
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise ConversationNotFound(str(conversation_id))
        conversation_key = str(conversation_id)

        self._bus.publish(conversation_key, "received", {"text": text})

        user_message = Message(conversation_id=conversation.id, role="user", content=text)
        session.add(user_message)
        session.commit()

        messages = [
            {"role": "system", "content": _system_prompt(self._tools)},
            *(
                {"role": message.role, "content": message.content}
                for message in conversation.messages
            ),
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
            # 真流式：网边生成边推 SSE（reply_chunk）。但工具调用是 JSON——
            # 不能边收边上屏，所以按首个非空白字符分类：'{'/'`' 开头视为
            # JSON（缓冲不上屏），其余（纯文本 final）逐段推送。
            stream_mode: dict[str, Any] = {"decided": None, "pending": []}

            def on_chunk(chunk: str) -> None:
                if stream_mode["decided"] == "buffer":
                    return
                if stream_mode["decided"] == "stream":
                    self._bus.publish(conversation_key, "reply_chunk", {"text": chunk})
                    return
                stream_mode["pending"].append(chunk)
                joined = "".join(stream_mode["pending"]).lstrip()
                if not joined:
                    return
                if joined[0] in "{`":
                    stream_mode["decided"] = "buffer"
                    return
                stream_mode["decided"] = "stream"
                self._bus.publish(conversation_key, "reply_chunk", {"text": joined})

            try:
                raw = self._llm.complete(messages, on_chunk=on_chunk)
            except LLMUnavailableError:
                # 模型服务不可达：如实推送 error 事件后上抛（API 映射 503）；
                # 不写半完成的 ReAct 状态（assistant 消息不落库）
                self._bus.publish(
                    conversation_key,
                    "error",
                    {"detail": "模型服务暂时不可用，请稍后重试"},
                )
                raise
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
                    # 用户可见提示通道（约定，见 Tool 协议）：非错误的显式业务结果
                    notice = getattr(tool, "notice", None)
                    if notice:
                        tool_outputs["notice"] = notice
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
