"""Ticket #8：Storyteller 讲解（语料库下线后：站点元数据直接生成）。

- HTTP 缝行为测试：行程每个圣地有讲解，文本含站名，citation 是 anitabi
  截图来源署名（origin/origin_url）；站点无 origin 时 citation 如实为空。
- 生成式讲解 LLM 故障回退模板拼句，不炸消息。
"""

import json

from fastapi.testclient import TestClient

from app.adapters.fakes import FakeLLMGateway, FakeSeichiRepository
from app.adapters.llm import LLMUnavailableError
from app.adapters.ports import Seichi
from app.adapters.providers import get_llm_gateway, get_seichi_repository
from app.agents.planner import ItineraryDay, ItinerarySnapshot
from app.agents.storyteller import narrate_itinerary
from app.main import app

WORK = "吹响吧！上低音号"
AREA = "宇治"


def _s(id: str, name: str, lat: float, lng: float) -> Seichi:
    return Seichi(
        id=id, name=name, work=WORK, area="宇治市", lat=lat, lng=lng,
        ep=1, ep_seconds=100, origin="fake", origin_url="https://example.com/src",
    )


FIXTURE = [
    _s("a1", "宇治桥", 34.8929, 135.8065),
    _s("a2", "宇治神社", 34.8905, 135.8099),
    _s("b1", "京阪六地藏", 34.9321, 135.7935),
]

PLAN_SCRIPT = [
    json.dumps(
        {"type": "tool_call", "name": "plan_itinerary",
         "args": {"ani_name": WORK, "area": AREA, "days": 3}}
    ),
    json.dumps({"type": "final", "content": "三天行程已生成"}),
]


def make_client() -> TestClient:
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway(scripted=list(PLAN_SCRIPT))
    app.dependency_overrides[get_seichi_repository] = lambda: FakeSeichiRepository(seichi=FIXTURE)
    return TestClient(app)


def plan(client: TestClient) -> dict:
    cid = client.post("/api/conversations").json()["conversation_id"]
    response = client.post(f"/api/conversations/{cid}/messages", json={"text": "宇治三天京吹"})
    assert response.status_code == 200
    return response.json()["itinerary"]


def test_每站都有讲解_文本含站名_署名来自origin():
    itinerary = plan(make_client())

    narrations = {
        n["seichi_id"]: n for d in itinerary["days"] for n in d["narrations"]
    }
    assert set(narrations) == {"a1", "a2", "b1"}
    for stop in FIXTURE:
        narration = narrations[stop.id]
        assert stop.name in narration["text"]  # 元数据拼句含站名
        assert WORK in narration["text"]  # 含作品名
        citation = narration["citation"]
        assert citation is not None, "有 origin 的站必须带来源署名"
        assert citation["source"] == "fake"
        assert citation["url"] == "https://example.com/src"


def test_站点无origin时citation如实为空():
    no_origin = Seichi(id="x1", name="无名地", work=WORK, area="宇治市",
                       lat=34.9, lng=135.8, ep=1)
    snapshot = ItinerarySnapshot(
        day_count=1, days=[ItineraryDay(day=1, seichi=[no_origin])], work=WORK, area=AREA,
    )

    narrate_itinerary(snapshot)

    narration = snapshot.days[0].narrations[0]
    assert "无名地" in narration.text
    assert narration.citation is None  # 无来源信息不编造署名


def test_讲解随快照持久化_刷新可见():
    client = make_client()
    cid = client.post("/api/conversations").json()["conversation_id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": "宇治三天京吹"})

    fresh = TestClient(app)
    itinerary = fresh.get(f"/api/conversations/{cid}/itinerary").json()["itinerary"]

    narrations = [n for d in itinerary["days"] for n in d["narrations"]]
    assert narrations
    assert all(n["citation"] for n in narrations)


def test_生成式讲解LLM故障时回退模板拼句():
    """live 网关抛 LLMUnavailableError（连接重试失败/4xx 包装）不炸消息：
    回退元数据拼句，citation 照常给。"""
    class FailingLLM:
        def complete(self, messages, on_chunk=None):
            raise LLMUnavailableError("connection error")

    snapshot = ItinerarySnapshot(
        day_count=1,
        days=[ItineraryDay(day=1, seichi=[FIXTURE[0]])],  # a1 宇治桥
        work=WORK,
        area=AREA,
    )

    narrate_itinerary(snapshot, llm=FailingLLM())

    narration = snapshot.days[0].narrations[0]
    assert "宇治桥" in narration.text  # 模板拼句
    assert narration.citation.source == "fake"


def test_生成式讲解用LLM输出():
    class StubLLM:
        def complete(self, messages, on_chunk=None):
            return "久美子放学路过的桥。"

    snapshot = ItinerarySnapshot(
        day_count=1, days=[ItineraryDay(day=1, seichi=[FIXTURE[0]])], work=WORK, area=AREA,
    )

    narrate_itinerary(snapshot, llm=StubLLM())

    assert snapshot.days[0].narrations[0].text == "久美子放学路过的桥。"
