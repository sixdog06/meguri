"""会话主干 API：创建会话、发送消息、读取历史、进度事件（SSE）。"""

import json
import queue
import uuid
from collections.abc import Iterator

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.ports import LLMGateway, SeichiRepository
from app.adapters.providers import get_llm_gateway, get_seichi_repository
from app.agents.events import EventBus, event_bus
from app.agents.orchestrator import ConversationNotFound, Orchestrator
from app.agents.tools import PlanItineraryTool, SearchSeichiTool, ToolRegistry
from app.agents.tracing import InMemoryTracer, Tracer
from app.db import get_session
from app.models import Conversation, Itinerary

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

# SSE 空闲超时（秒）：这么久没有新事件就结束流，浏览器 EventSource 会自动重连。
# 测试里 monkeypatch 成小值。
SSE_IDLE_TIMEOUT = 30.0

# 默认 tracer 为进程内内存实现，测试/评测可 override 检查。
_default_tracer = InMemoryTracer()


class CreateConversationResponse(BaseModel):
    conversation_id: str


class PostMessageRequest(BaseModel):
    text: str


class SeichiCandidate(BaseModel):
    """候选圣地：名称、坐标、对照截图引用、出处（集数+截图来源）。

    与 ports.Seichi 字段一一对应（payload 里存的是它的序列化 dict）。"""

    id: str | None = None
    name: str
    work: str | None = None
    area: str | None = None
    lat: float
    lng: float
    image: str | None = None  # 对照截图（缩略图 URL）
    ep: int | str | None = None  # 出处集数（可能为 "OST" 等）
    ep_seconds: int | None = None  # 截图在集内的时间（秒）
    origin: str | None = None  # 截图来源（CC BY-NC-SA 要求标注）
    origin_url: str | None = None


class TransitLegOut(BaseModel):
    """交通段：相邻两个圣地之间（或天与天之间）的衔接。

    schema（mode/duration_minutes/fare_yen/estimate）即 #6 OTP 的数据契约；
    本票为距离估算（estimate=True），fare_yen 留空。"""

    from_id: str  # 圣地 id（无 id 时为快照内序号）
    to_id: str
    mode: str  # walk / drive
    distance_km: float
    duration_minutes: int
    estimate: bool
    fare_yen: int | None = None
    cross_day: bool = False  # True = 每天末尾到次日开头的连接段


class ItineraryDayOut(BaseModel):
    day: int
    seichi: list[SeichiCandidate]
    legs: list[TransitLegOut]


class ItineraryOut(BaseModel):
    """行程快照：按天组织的圣地序列 + 交通段；预算只留结构（后续票填值）。"""

    work: str | None = None
    area: str | None = None
    day_count: int
    days: list[ItineraryDayOut]
    budget: dict | None = None


class ItineraryResponse(BaseModel):
    itinerary: ItineraryOut | None = None


class PostMessageResponse(BaseModel):
    reply: str
    seichi: list[SeichiCandidate] = []  # 本轮检索出的结构化候选圣地
    itinerary: ItineraryOut | None = None  # 本轮生成的行程快照


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    payload: dict[str, Any] | None = None


def get_tool_registry(
    repository: SeichiRepository = Depends(get_seichi_repository),
) -> ToolRegistry:
    """生产 wiring：注册 Scout 的 search_seichi 工具（#4）。

    每请求构建，SeichiRepository 经 FastAPI 依赖注入——测试在 HTTP 缝
    override get_seichi_repository 即换 fake 数据源。
    """
    registry = ToolRegistry()
    registry.register(SearchSeichiTool(repository))
    registry.register(PlanItineraryTool(repository))
    return registry


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
    # 结构化结果按工具名收集（见 Tool 协议 structured 约定通道）
    payload = assistant_message.payload or {}
    return PostMessageResponse(
        reply=assistant_message.content,
        seichi=payload.get("search_seichi", []),
        itinerary=payload.get("plan_itinerary"),
    )


@router.get("/{conversation_id}/itinerary", response_model=ItineraryResponse)
def get_itinerary(
    conversation_id: uuid.UUID = Depends(valid_conversation_id),
    session: Session = Depends(get_session),
) -> ItineraryResponse:
    """读取会话最新一份行程快照（刷新页面后恢复行程视图）；没有则为 null。"""
    _get_conversation_or_404(conversation_id, session)
    itinerary = (
        session.query(Itinerary)
        .filter_by(conversation_id=conversation_id)
        .order_by(Itinerary.created_at.desc())
        .first()
    )
    # 空快照（{}）是规划失败的占位，对外等价于“没有行程”
    return ItineraryResponse(itinerary=(itinerary.payload or None) if itinerary else None)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def get_messages(
    conversation_id: uuid.UUID = Depends(valid_conversation_id),
    session: Session = Depends(get_session),
) -> list[MessageOut]:
    conversation = _get_conversation_or_404(conversation_id, session)
    return [
        MessageOut(id=m.id, role=m.role, content=m.content, payload=m.payload)
        for m in conversation.messages
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
