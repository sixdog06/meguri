"""会话主干 API：创建会话、发送消息、读取历史、进度事件（SSE）。"""

import json
import queue
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.ports import LLMGateway
from app.adapters.providers import get_llm_gateway
from app.agents.events import EventBus, event_bus
from app.agents.orchestrator import ConversationNotFound, Orchestrator
from app.agents.tools import ToolRegistry
from app.agents.tracing import InMemoryTracer, Tracer
from app.db import get_session
from app.models import Conversation

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

# SSE 空闲超时（秒）：这么久没有新事件就结束流，浏览器 EventSource 会自动重连。
# 测试里 monkeypatch 成小值。
SSE_IDLE_TIMEOUT = 30.0

# 生产 wiring：工具注册表为空（后续 ticket 注册具体工具）；
# 默认 tracer 为进程内内存实现，测试/评测可 override 检查。
_tool_registry = ToolRegistry()
_default_tracer = InMemoryTracer()


class CreateConversationResponse(BaseModel):
    conversation_id: str


class PostMessageRequest(BaseModel):
    text: str


class PostMessageResponse(BaseModel):
    reply: str


class MessageOut(BaseModel):
    id: int
    role: str
    content: str


def get_tool_registry() -> ToolRegistry:
    return _tool_registry


def get_tracer() -> Tracer:
    return _default_tracer


def get_event_bus() -> EventBus:
    return event_bus


def get_orchestrator(
    llm: LLMGateway = Depends(get_llm_gateway),
    tools: ToolRegistry = Depends(get_tool_registry),
    tracer: Tracer = Depends(get_tracer),
) -> Orchestrator:
    return Orchestrator(llm, tools=tools, tracer=tracer)


def valid_conversation_id(conversation_id: str) -> uuid.UUID:
    """路由层统一校验会话 ID：畸形 ID 一律 404，不放进 Orchestrator。"""
    try:
        return uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="会话不存在") from None


def _get_conversation_or_404(conversation_id: uuid.UUID, session: Session) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation


@router.post("", response_model=CreateConversationResponse)
def create_conversation(session: Session = Depends(get_session)) -> CreateConversationResponse:
    conversation = Conversation()
    session.add(conversation)
    session.commit()
    return CreateConversationResponse(conversation_id=str(conversation.id))


@router.post("/{conversation_id}/messages", response_model=PostMessageResponse)
def post_message(
    body: PostMessageRequest,
    conversation_id: uuid.UUID = Depends(valid_conversation_id),
    orchestrator: Orchestrator = Depends(get_orchestrator),
    session: Session = Depends(get_session),
) -> PostMessageResponse:
    try:
        assistant_message = orchestrator.reply(session, conversation_id, body.text)
    except ConversationNotFound:
        raise HTTPException(status_code=404, detail="会话不存在") from None
    return PostMessageResponse(reply=assistant_message.content)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def get_messages(
    conversation_id: uuid.UUID = Depends(valid_conversation_id),
    session: Session = Depends(get_session),
) -> list[MessageOut]:
    conversation = _get_conversation_or_404(conversation_id, session)
    return [
        MessageOut(id=m.id, role=m.role, content=m.content) for m in conversation.messages
    ]


@router.get("/{conversation_id}/events")
def stream_events(
    conversation_id: uuid.UUID = Depends(valid_conversation_id),
    bus: EventBus = Depends(get_event_bus),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """SSE：订阅某会话的进度事件。

    收到 done 不关闭流——同一连接继续推后续回合的事件，由客户端断开；
    SSE_IDLE_TIMEOUT 秒无事件则结束流（浏览器 EventSource 会自动重连，
    回放最近的 backlog）。
    """
    _get_conversation_or_404(conversation_id, session)
    events = bus.subscribe(str(conversation_id))

    def event_stream() -> Iterator[str]:
        while True:
            try:
                item = events.get(timeout=SSE_IDLE_TIMEOUT)
            except queue.Empty:
                return
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
