"""会话主干 API：创建会话、发送消息、读取历史、进度事件（SSE）。"""

import json
import queue
import uuid
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.ports import (
    CorpusStore,
    LLMGateway,
    OpeningHoursSource,
    SeichiRepository,
    TransitClient,
)
from app.adapters.providers import (
    generative_llm,
    get_corpus_store,
    get_llm_gateway,
    get_opening_hours_source,
    get_seichi_repository,
    get_transit_client,
)
from app.agents.editing import Edit, InvalidEditError, UnknownSeichiError, apply_edit
from app.agents.events import EventBus, event_bus
from app.agents.orchestrator import ConversationNotFound, Orchestrator
from app.agents.planner import snapshot_from_dict
from app.agents.revalidate import revalidate_snapshot
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
    OTP 查询失败/未覆盖时保留估算并 degraded=True 显式降级。"""

    from_id: str  # 圣地 id（无 id 时为快照内序号）
    to_id: str
    mode: str  # walk / drive（估算）/ transit（OTP 真实）
    distance_km: float
    duration_minutes: int
    estimate: bool
    fare_yen: int | None = None
    cross_day: bool = False  # True = 每天末尾到次日开头的连接段
    degraded: bool = False  # True = 交通查询失败/未覆盖，已保留估算（降级提示）
    note: str | None = None


class StopCheckOut(BaseModel):
    """单站时间校验：计划到达时间 + 开放时间（None = 未知不误标）。"""

    seichi_id: str
    arrive_time: str
    open: bool | None = None
    note: str | None = None


class CitationOut(BaseModel):
    """讲解引用的语料出处。"""

    chunk_id: str
    source: str


class NarrationOut(BaseModel):
    """单站讲解（#8）：检索语料原文片段 + citation。"""

    seichi_id: str
    text: str
    citation: CitationOut | None = None


class ItineraryDayOut(BaseModel):
    day: int
    seichi: list[SeichiCandidate]
    legs: list[TransitLegOut]
    checks: list[StopCheckOut] = []
    narrations: list[NarrationOut] = []


class BudgetItemOut(BaseModel):
    label: str
    amount_yen: int | None = None  # None = 未计价（不计入合计，不静默当 0）


class BudgetOut(BaseModel):
    """预算报告（#7 确定性预算服务）：总计、交通/门票分项、超支告警。"""

    limit_yen: int | None = None
    total_yen: int  # 已计价合计
    over_budget: bool
    alert: str | None = None
    transit: list[BudgetItemOut] = []
    admission: list[BudgetItemOut] = []
    unpriced_count: int = 0  # 未计价项数（票价缺失的交通段 + 无数据源的门票）


class ItineraryOut(BaseModel):
    """行程快照：按天组织的圣地序列 + 交通段 + 预算报告。"""

    work: str | None = None
    area: str | None = None
    day_count: int
    days: list[ItineraryDayOut]
    budget: BudgetOut | None = None


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
    transit: TransitClient = Depends(get_transit_client),
    hours: OpeningHoursSource = Depends(get_opening_hours_source),
    corpus: CorpusStore = Depends(get_corpus_store),
    llm: LLMGateway = Depends(get_llm_gateway),
) -> ToolRegistry:
    """生产 wiring：注册 Scout 的 search_seichi 工具（#4）与 Planner 的
    plan_itinerary 工具（#5；#6 Navigator 交通校验、#7 预算、#8 Storyteller）。

    每请求构建，外部依赖经 FastAPI 依赖注入——测试在 HTTP 缝
    override 对应 provider 即换 fake。生成式讲解按 LLM 能力标志启用
    （generative_capable，见 providers.generative_llm）。
    """
    registry = ToolRegistry()
    registry.register(SearchSeichiTool(repository))
    registry.register(
        PlanItineraryTool(repository, transit, hours, corpus, generative_llm(llm))
    )
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
    row = (
        session.query(Itinerary)
        .filter_by(conversation_id=conversation_id)
        .order_by(Itinerary.created_at.desc(), Itinerary.id.desc())  # id 作次序 tiebreak
        .first()
    )
    # 空快照（{}）是规划失败的占位，对外等价于“没有行程”
    return ItineraryResponse(itinerary=(row.payload or None) if row else None)


class CandidatesResponse(BaseModel):
    candidates: list[SeichiCandidate]


def _latest_itinerary_payload(conversation_id: uuid.UUID, session: Session) -> dict:
    """最新一份有效（非占位）行程快照 payload；没有则 404。"""
    _get_conversation_or_404(conversation_id, session)
    row = (
        session.query(Itinerary)
        .filter_by(conversation_id=conversation_id)
        .order_by(Itinerary.created_at.desc(), Itinerary.id.desc())  # id 作次序 tiebreak
        .first()
    )
    if row is None or not row.payload:
        raise HTTPException(status_code=404, detail="当前会话没有行程快照")
    return row.payload


@router.get("/{conversation_id}/itinerary/candidates", response_model=CandidatesResponse)
def get_candidates(
    conversation_id: uuid.UUID = Depends(valid_conversation_id),
    session: Session = Depends(get_session),
    repository: SeichiRepository = Depends(get_seichi_repository),
) -> CandidatesResponse:
    """“添加圣地”的候选列表：同作品/地区检索结果中排除已在行程内的。"""
    payload = _latest_itinerary_payload(conversation_id, session)
    in_itinerary = {str(s["id"]) for d in payload["days"] for s in d["seichi"]}
    results = repository.search_seichi(payload.get("work") or "", payload.get("area") or "")
    return CandidatesResponse(
        candidates=[asdict(s) for s in results if str(s.id) not in in_itinerary]
    )


@router.post("/{conversation_id}/itinerary/edits", response_model=ItineraryResponse)
def edit_itinerary(
    body: Edit,
    conversation_id: uuid.UUID = Depends(valid_conversation_id),
    session: Session = Depends(get_session),
    repository: SeichiRepository = Depends(get_seichi_repository),
    transit: TransitClient = Depends(get_transit_client),
    hours: OpeningHoursSource = Depends(get_opening_hours_source),
    corpus: CorpusStore = Depends(get_corpus_store),
    llm: LLMGateway = Depends(get_llm_gateway),
) -> ItineraryResponse:
    """应用一次编辑操作 → 自动重校验（revalidate 管线）→ 新快照落库返回。"""
    payload = _latest_itinerary_payload(conversation_id, session)
    snapshot = snapshot_from_dict(payload)

    candidates = []
    if body.type == "add":
        candidates = repository.search_seichi(snapshot.work or "", snapshot.area or "")
    try:
        apply_edit(snapshot, body, candidates)
    except UnknownSeichiError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except InvalidEditError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    revalidate_snapshot(
        snapshot,
        transit=transit,
        hours=hours,
        corpus=corpus,
        limit_yen=(payload.get("budget") or {}).get("limit_yen"),
        llm=generative_llm(llm),
    )

    new_payload = asdict(snapshot)
    session.add(Itinerary(conversation_id=conversation_id, payload=new_payload))
    session.commit()
    return ItineraryResponse(itinerary=new_payload)


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
