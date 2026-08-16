"""Ticket #9：行程编辑与自动重新校验。

行为测试经 HTTP 缝驱动：编辑端点应用操作 → 自动重跑 Navigator 校验 +
预算重算 + Storyteller 补讲解 → 新快照落库返回。
"""

import json

from fastapi.testclient import TestClient

from app.adapters.fakes import FakeLLMGateway, FakeSeichiRepository, FakeTransitClient
from app.adapters.ports import CorpusChunk, Seichi
from app.adapters.providers import (
    get_corpus_store,
    get_llm_gateway,
    get_seichi_repository,
    get_transit_client,
)
from app.main import app
from app.rag.store import InMemoryCorpusStore

WORK = "吹响吧！上低音号"
AREA = "宇治"


def _s(id: str, name: str, lat: float, lng: float) -> Seichi:
    return Seichi(
        id=id, name=name, work=WORK, area="宇治市", lat=lat, lng=lng,
        ep=1, ep_seconds=100, origin="fake",
    )


A1 = _s("a1", "宇治桥", 34.8929, 135.8065)
A3 = _s("a3", "久美子椅", 34.8896, 135.8075)
A2 = _s("a2", "宇治神社", 34.8905, 135.8099)
B1 = _s("b1", "京阪六地藏", 34.9321, 135.7935)
B2 = _s("b2", "六地藏站旁铁塔", 34.9315, 135.7941)
C1 = _s("c1", "山城综合运动公园", 34.8714, 135.8054)
X1 = _s("x1", "喜撰桥", 34.8881, 135.8099)  # 追加候选（编辑"增"用）

FIXTURE = [A1, A2, A3, B1, B2, C1]

PLAN_SCRIPT = [
    json.dumps(
        {"type": "tool_call", "name": "plan_itinerary",
         "args": {"work": WORK, "area": AREA, "days": 3}}
    ),
    json.dumps({"type": "final", "content": "三天行程已生成"}),
]

CHUNKS = [
    CorpusChunk(id="ck-a1", source="anitabi", work=WORK,
                text="宇治桥是《吹响吧！上低音号》久美子放学路过的桥。"),
    CorpusChunk(id="ck-x1", source="anitabi", work=WORK,
                text="喜撰桥是《吹响吧！上低音号》宇治川上的另一座名场面桥。"),
]


def make_client(
    repo: FakeSeichiRepository | None = None,
    transit: FakeTransitClient | None = None,
) -> TestClient:
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway(scripted=list(PLAN_SCRIPT))
    app.dependency_overrides[get_seichi_repository] = lambda: repo or FakeSeichiRepository(seichi=FIXTURE)
    app.dependency_overrides[get_transit_client] = lambda: transit or FakeTransitClient()
    app.dependency_overrides[get_corpus_store] = lambda: InMemoryCorpusStore(chunks=CHUNKS)
    return TestClient(app)


def plan(client: TestClient) -> tuple[str, dict]:
    cid = client.post("/api/conversations").json()["conversation_id"]
    response = client.post(f"/api/conversations/{cid}/messages", json={"text": "宇治三天京吹"})
    assert response.status_code == 200
    return cid, response.json()["itinerary"]


def edit(client: TestClient, cid: str, body: dict, expect: int = 200) -> dict:
    response = client.post(f"/api/conversations/{cid}/itinerary/edits", json=body)
    assert response.status_code == expect, response.text
    return response.json()


def day_of(itinerary: dict, seichi_id: str) -> dict:
    for d in itinerary["days"]:
        if any(s["id"] == seichi_id for s in d["seichi"]):
            return d
    raise AssertionError(f"{seichi_id} 不在行程中")


def test_删除圣地_legs重算_讲解消失():
    client = make_client()
    cid, _ = plan(client)

    result = edit(client, cid, {"type": "remove", "seichi_id": "a3"})

    itinerary = result["itinerary"]
    day2 = day_of(itinerary, "a1")
    assert [s["id"] for s in day2["seichi"]] == ["a1", "a2"]  # a3 被删
    # 失效交通段被替换：a3→a2 段消失，新 a1→a2 段出现
    pairs = [(leg["from_id"], leg["to_id"]) for leg in day2["legs"] if not leg["cross_day"]]
    assert pairs == [("a1", "a2")]
    # 被删站的讲解消失
    narrations = [n for d in itinerary["days"] for n in d["narrations"]]
    assert all(n["seichi_id"] != "a3" for n in narrations)
    # 新快照落库
    fresh = TestClient(app)
    assert fresh.get(f"/api/conversations/{cid}/itinerary").json()["itinerary"] == itinerary


def test_添加圣地_从候选按id加入并补讲解():
    repo = FakeSeichiRepository(seichi=FIXTURE)
    client = make_client(repo=repo)
    cid, _ = plan(client)
    # 规划后候选集里出现新圣地（模拟候选刷新）
    app.dependency_overrides[get_seichi_repository] = lambda: FakeSeichiRepository(seichi=FIXTURE + [X1])

    result = edit(client, cid, {"type": "add", "day": 3, "seichi_id": "x1"})

    itinerary = result["itinerary"]
    day3 = day_of(itinerary, "x1")
    assert day3["day"] == 3
    assert [s["id"] for s in day3["seichi"]] == ["c1", "x1"]  # 追加到当天末尾
    # 新站有交通段和讲解（经 Storyteller 检索）
    pairs = [(leg["from_id"], leg["to_id"]) for leg in day3["legs"] if not leg["cross_day"]]
    assert pairs == [("c1", "x1")]
    narration = next(n for n in day3["narrations"] if n["seichi_id"] == "x1")
    assert narration["citation"]["chunk_id"] == "ck-x1"
    # 未受影响的讲解保留
    day2 = day_of(itinerary, "a1")
    kept = next(n for n in day2["narrations"] if n["seichi_id"] == "a1")
    assert kept["citation"]["chunk_id"] == "ck-a1"


def test_改序_天内调整顺序():
    client = make_client()
    cid, before = plan(client)
    day2 = day_of(before, "a1")
    assert [s["id"] for s in day2["seichi"]] == ["a1", "a3", "a2"]

    result = edit(client, cid, {"type": "reorder", "day": 2, "seichi_ids": ["a2", "a3", "a1"]})

    day2 = day_of(result["itinerary"], "a1")
    assert [s["id"] for s in day2["seichi"]] == ["a2", "a3", "a1"]
    pairs = [(leg["from_id"], leg["to_id"]) for leg in day2["legs"] if not leg["cross_day"]]
    assert pairs == [("a2", "a3"), ("a3", "a1")]


def test_换天_圣地移到另一天():
    client = make_client()
    cid, _ = plan(client)

    result = edit(client, cid, {"type": "move_day", "seichi_id": "a1", "to_day": 1})

    itinerary = result["itinerary"]
    assert day_of(itinerary, "a1")["day"] == 1
    # 天号连续无空缺
    assert [d["day"] for d in itinerary["days"]] == list(range(1, itinerary["day_count"] + 1))


def test_编辑后新交通段被真实数据替换():
    """OTP 可达时：编辑产生的失效段被真实查询结果替换（不只结构重排）。"""
    # 规划 5 段 + 删除后 4 段，全部返回真实结果
    real = {"mode": "transit", "duration_minutes": 15, "fare_yen": 200, "estimate": False}
    transit = FakeTransitClient(scripted=[dict(real) for _ in range(9)])
    client = make_client(transit=transit)
    cid, _ = plan(client)

    result = edit(client, cid, {"type": "remove", "seichi_id": "a3"})

    legs = [leg for d in result["itinerary"]["days"] for leg in d["legs"]]
    assert len(legs) == 4
    assert len(transit.calls) == 9  # 规划 5 + 编辑后重算 4
    for leg in legs:
        assert leg["estimate"] is False
        assert leg["mode"] == "transit"


def test_候选列表端点_排除已在行程中的圣地():
    repo = FakeSeichiRepository(seichi=FIXTURE)
    client = make_client(repo=repo)
    cid, _ = plan(client)
    # 规划后候选集里出现新圣地（模拟候选刷新）
    app.dependency_overrides[get_seichi_repository] = lambda: FakeSeichiRepository(seichi=FIXTURE + [X1])

    response = client.get(f"/api/conversations/{cid}/itinerary/candidates")

    assert response.status_code == 200
    candidates = response.json()["candidates"]
    ids = [c["id"] for c in candidates]
    assert "x1" in ids
    assert "a1" not in ids  # 已在行程中的不出现


def test_非法编辑_不存在的圣地():
    client = make_client()
    cid, _ = plan(client)

    edit(client, cid, {"type": "remove", "seichi_id": "nobody"}, expect=404)
    edit(client, cid, {"type": "add", "day": 1, "seichi_id": "nobody"}, expect=404)


def test_非法编辑_不存在的天与改序参数():
    client = make_client()
    cid, _ = plan(client)

    edit(client, cid, {"type": "move_day", "seichi_id": "a1", "to_day": 99}, expect=422)
    edit(client, cid, {"type": "reorder", "day": 2, "seichi_ids": ["a1"]}, expect=422)  # 数量不符
    edit(client, cid, {"type": "add", "day": 99, "seichi_id": "a1"}, expect=422)
