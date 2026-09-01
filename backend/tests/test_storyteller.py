"""Ticket #8：RAG 语料库 + Storyteller 讲解。

- HTTP 缝行为测试：fake CorpusStore（固定 chunks）→ 行程每个圣地有讲解且
  带 citation，citation 对应真实检索结果；不依赖 OTP/预算（fake 全关也工作）。
- 语料为空时不编造讲解（零幻觉）。
"""

import json

from fastapi.testclient import TestClient

from app.adapters.fakes import FakeLLMGateway, FakeSeichiRepository
from app.adapters.llm import LLMUnavailableError
from app.adapters.ports import CorpusChunk, Seichi
from app.adapters.providers import get_corpus_store, get_llm_gateway, get_seichi_repository
from app.agents.planner import ItineraryDay, ItinerarySnapshot
from app.agents.storyteller import narrate_itinerary
from app.main import app
from app.rag.store import InMemoryCorpusStore

WORK = "吹响吧！上低音号"
AREA = "宇治"


def _s(id: str, name: str, lat: float, lng: float) -> Seichi:
    return Seichi(
        id=id, name=name, work=WORK, area="宇治市", lat=lat, lng=lng,
        ep=1, ep_seconds=100, origin="fake",
    )


FIXTURE = [
    _s("a1", "宇治桥", 34.8929, 135.8065),
    _s("a2", "宇治神社", 34.8905, 135.8099),
    _s("b1", "京阪六地藏", 34.9321, 135.7935),
]

CHUNKS = [
    CorpusChunk(
        id="anitabi:115908:a1",
        source="anitabi",
        work=WORK,
        text="宇治桥是《吹响吧！上低音号》第一集久美子放学路过的桥，桥下是宇治川。",
    ),
    CorpusChunk(
        id="anitabi:115908:a2",
        source="anitabi",
        work=WORK,
        text="宇治神社在剧中多次出现，是久美子与丽奈商量事情的地方。",
    ),
    CorpusChunk(
        id="bangumi:115908",
        source="bangumi.tv",
        work=WORK,
        text="《吹响吧！上低音号》讲述北宇治高中吹奏乐部以全国大赛为目标的故事。",
    ),
]

PLAN_SCRIPT = [
    json.dumps(
        {"type": "tool_call", "name": "plan_itinerary",
         "args": {"ani_name": WORK, "area": AREA, "days": 3}}
    ),
    json.dumps({"type": "final", "content": "三天行程已生成"}),
]


def make_client(store: InMemoryCorpusStore) -> TestClient:
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway(scripted=list(PLAN_SCRIPT))
    app.dependency_overrides[get_seichi_repository] = lambda: FakeSeichiRepository(seichi=FIXTURE)
    app.dependency_overrides[get_corpus_store] = lambda: store
    return TestClient(app)


def plan(client: TestClient) -> dict:
    cid = client.post("/api/conversations").json()["conversation_id"]
    response = client.post(f"/api/conversations/{cid}/messages", json={"text": "宇治三天京吹"})
    assert response.status_code == 200
    return response.json()["itinerary"]


def test_讲解必须达标才产出_相关站有讲解且引用真实语料():
    client = make_client(InMemoryCorpusStore(chunks=CHUNKS))

    itinerary = plan(client)

    chunk_by_id = {c.id: c for c in CHUNKS}
    narrations = {
        n["seichi_id"]: n for d in itinerary["days"] for n in d["narrations"]
    }
    # 有相关语料的站：讲解带 citation，且 citation 对应真实检索结果
    for stop_id in ("a1", "a2"):
        narration = narrations[stop_id]
        assert narration["text"]
        citation = narration["citation"]
        assert citation is not None, "讲解必须带 citation"
        chunk = chunk_by_id[citation["chunk_id"]]
        assert citation["source"] == chunk.source
        # 讲解文本是检索到的语料原文片段（检索式拼装，非自由发挥）
        assert narration["text"].startswith(chunk.text[:20])


def test_语料相关度不达标时不产出讲解():
    """相似度阈值：语料非空但与本站无关（含同作品泛条目）→ 无命中不产出，
    citation 不给错配背书。"""
    client = make_client(InMemoryCorpusStore(chunks=CHUNKS))

    itinerary = plan(client)

    narrations = {
        n["seichi_id"]: n for d in itinerary["days"] for n in d["narrations"]
    }
    # b1 京阪六地藏：语料库里只有泛作品条目（相关度低于阈值）→ 不产出
    assert "b1" not in narrations


def test_语料全部无关时一个讲解都没有():
    unrelated = [
        CorpusChunk(id="c-x", source="anitabi", work="轻音少女",
                    text="丰乡小学校旧址是轻音少女社团活动室原型。")
    ]
    client = make_client(InMemoryCorpusStore(chunks=unrelated))

    itinerary = plan(client)

    narrations = [n for d in itinerary["days"] for n in d["narrations"]]
    assert narrations == []


def test_讲解随快照持久化_刷新可见():
    client = make_client(InMemoryCorpusStore(chunks=CHUNKS))
    cid = client.post("/api/conversations").json()["conversation_id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": "宇治三天京吹"})

    fresh = TestClient(app)
    itinerary = fresh.get(f"/api/conversations/{cid}/itinerary").json()["itinerary"]

    narrations = [n for d in itinerary["days"] for n in d["narrations"]]
    assert narrations
    assert all(n["citation"] for n in narrations)


def test_语料为空时不编造讲解():
    """零幻觉：检索不到语料就不产出讲解（citation 为空），行程其余部分不受影响。"""
    client = make_client(InMemoryCorpusStore(chunks=[]))

    itinerary = plan(client)

    assert itinerary["day_count"] == 3  # 行程本身正常（不依赖语料）
    narrations = [n for d in itinerary["days"] for n in d["narrations"]]
    assert narrations == [] or all(n["citation"] is None for n in narrations)


def test_生成式讲解LLM故障时回退检索式摘录():
    """live 网关抛 LLMUnavailableError（连接重试失败/4xx 包装）不炸消息：
    回退 top-1 语料摘录，citation 照常给。"""
    class FailingLLM:
        def complete(self, messages, on_chunk=None):
            raise LLMUnavailableError("connection error")

    snapshot = ItinerarySnapshot(
        day_count=1,
        days=[ItineraryDay(day=1, seichi=[FIXTURE[0]])],  # a1 宇治桥
        work=WORK,
        area=AREA,
    )

    narrate_itinerary(snapshot, InMemoryCorpusStore(chunks=CHUNKS), llm=FailingLLM())

    narration = snapshot.days[0].narrations[0]
    assert narration.text.startswith(CHUNKS[0].text[:20])  # top-1 语料原文摘录
    assert narration.citation.chunk_id == CHUNKS[0].id
