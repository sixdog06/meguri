"""Ticket #5：Planner 行程生成 + 行程快照。

行为测试经 HTTP 缝驱动；数据源为固定数据集的 FakeSeichiRepository。
聚类夹具：6 个圣地分 3 个地理簇（六地藏 B / 宇治中心 A / 城南 C），
三天规划应每簇一天。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeLLMGateway, FakeSeichiRepository
from app.adapters.ports import Seichi
from app.adapters.providers import get_llm_gateway, get_seichi_repository
from app.api import conversations
from app.main import app

WORK = "吹响吧！上低音号"
AREA = "宇治"
DAYS = 3


def _s(id: str, name: str, lat: float, lng: float) -> Seichi:
    return Seichi(
        id=id, name=name, work=WORK, area="宇治市", lat=lat, lng=lng,
        ep=1, ep_seconds=100, origin="fake",
    )


# 宇治中心簇（A）：最北 a1 → 最近邻 a3 → a2
A1 = _s("a1", "宇治桥", 34.8929, 135.8065)
A3 = _s("a3", "久美子椅", 34.8896, 135.8075)
A2 = _s("a2", "宇治神社", 34.8905, 135.8099)
# 六地藏簇（B）
B1 = _s("b1", "京阪六地藏", 34.9321, 135.7935)
B2 = _s("b2", "六地藏站旁铁塔", 34.9315, 135.7941)
# 城南簇（C）
C1 = _s("c1", "山城综合运动公园", 34.8714, 135.8054)

FIXTURE = [A1, A2, A3, B1, B2, C1]


def plan_script(days: int = DAYS) -> list[str]:
    return [
        json.dumps(
            {
                "type": "tool_call",
                "name": "plan_itinerary",
                "args": {"work": WORK, "area": AREA, "days": days},
            }
        ),
        json.dumps({"type": "final", "content": "三天行程已生成"}),
    ]


def make_client(repo: FakeSeichiRepository, scripted: list[str] | None = None) -> TestClient:
    gateway = FakeLLMGateway(scripted=scripted or plan_script())
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    app.dependency_overrides[get_seichi_repository] = lambda: repo
    return TestClient(app)


def create_conversation(client: TestClient) -> str:
    response = client.post("/api/conversations")
    assert response.status_code == 200
    return response.json()["conversation_id"]


def post_message(client: TestClient, conversation_id: str, text: str) -> dict:
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"text": text},
    )
    assert response.status_code == 200
    return response.json()


def intra_legs(day: dict) -> list[dict]:
    return [leg for leg in day["legs"] if not leg["cross_day"]]


def test_N天请求生成按天组织的行程快照():
    client = make_client(FakeSeichiRepository(seichi=FIXTURE))
    conversation_id = create_conversation(client)

    body = post_message(client, conversation_id, "宇治三天京吹")

    assert body["reply"] == "三天行程已生成"
    itinerary = body["itinerary"]
    assert itinerary["work"] == WORK
    assert itinerary["day_count"] == DAYS
    assert [d["day"] for d in itinerary["days"]] == [1, 2, 3]


def test_每天圣地来自候选集且全覆盖():
    client = make_client(FakeSeichiRepository(seichi=FIXTURE))
    conversation_id = create_conversation(client)

    itinerary = post_message(client, conversation_id, "宇治三天京吹")["itinerary"]

    names = [s["name"] for d in itinerary["days"] for s in d["seichi"]]
    assert sorted(names) == sorted(s.name for s in FIXTURE)


def test_天内顺序为最近邻优化():
    client = make_client(FakeSeichiRepository(seichi=FIXTURE))
    conversation_id = create_conversation(client)

    itinerary = post_message(client, conversation_id, "宇治三天京吹")["itinerary"]

    by_size = {len(d["seichi"]): d for d in itinerary["days"]}
    # 宇治中心簇：从最北点出发最近邻 → 宇治桥 → 久美子椅 → 宇治神社
    assert [s["name"] for s in by_size[3]["seichi"]] == ["宇治桥", "久美子椅", "宇治神社"]
    # 六地藏簇
    assert [s["name"] for s in by_size[2]["seichi"]] == ["京阪六地藏", "六地藏站旁铁塔"]
    # 单点簇（最南，为最后一天）无天内段也无跨天段
    assert by_size[1]["legs"] == []


def test_天的排序自北往南():
    """南北分明的数据集：Day 1 必须是北簇。"""
    north = _s("n1", "北点", 35.0, 135.8)
    mid = _s("m1", "中点", 34.9, 135.8)
    south = _s("s1", "南点", 34.8, 135.8)
    client = make_client(FakeSeichiRepository(seichi=[south, north, mid]))
    conversation_id = create_conversation(client)

    itinerary = post_message(client, conversation_id, "宇治三天京吹")["itinerary"]

    assert [[s["name"] for s in d["seichi"]] for d in itinerary["days"]] == [
        ["北点"],
        ["中点"],
        ["南点"],
    ]


def test_倾斜分布下空簇被修复_天数等于请求天数():
    """3 个同坐标点 + 1 个远点，请求 3 天：同坐标会让 k-means 产生空簇
    （种子重合、指派平局），必须被修复为非空切分。"""
    same = [_s(f"d{i}", f"同点{i}", 34.890, 135.806) for i in range(3)]
    far = _s("f1", "远点", 34.98, 135.86)
    client = make_client(FakeSeichiRepository(seichi=same + [far]))
    conversation_id = create_conversation(client)

    itinerary = post_message(client, conversation_id, "宇治三天京吹")["itinerary"]

    assert itinerary["day_count"] == DAYS
    assert all(len(d["seichi"]) >= 1 for d in itinerary["days"])
    names = [s["name"] for d in itinerary["days"] for s in d["seichi"]]
    assert sorted(names) == sorted([*(s.name for s in same), "远点"])


def test_天数超过候选数时每天一个():
    client = make_client(
        FakeSeichiRepository(seichi=[A1, C1]),
        scripted=plan_script(days=5),
    )
    conversation_id = create_conversation(client)

    itinerary = post_message(client, conversation_id, "宇治五天京吹")["itinerary"]

    assert itinerary["day_count"] == 2  # min(请求天数, 候选数)


def test_交通段带估算标记且用圣地id引用():
    client = make_client(FakeSeichiRepository(seichi=FIXTURE))
    conversation_id = create_conversation(client)

    itinerary = post_message(client, conversation_id, "宇治三天京吹")["itinerary"]

    legs = [leg for d in itinerary["days"] for leg in d["legs"]]
    for leg in legs:
        assert leg["estimate"] is True
        assert leg["mode"] in ("walk", "drive")
        assert leg["duration_minutes"] >= 1
        assert leg["distance_km"] > 0
        assert leg["fare_yen"] is None  # #6 OTP 票填值
    # 天内段用圣地 id 引用（不是名字字符串）
    by_size = {len(d["seichi"]): d for d in itinerary["days"]}
    day_a = by_size[3]
    assert [(leg["from_id"], leg["to_id"]) for leg in intra_legs(day_a)] == [("a1", "a3"), ("a3", "a2")]
    assert all(leg["mode"] == "walk" for leg in intra_legs(day_a))


def test_天与天之间有跨天交通段():
    client = make_client(FakeSeichiRepository(seichi=FIXTURE))
    conversation_id = create_conversation(client)

    itinerary = post_message(client, conversation_id, "宇治三天京吹")["itinerary"]

    connectors = [leg for d in itinerary["days"] for leg in d["legs"] if leg["cross_day"]]
    assert len(connectors) == DAYS - 1
    for leg in connectors:
        assert leg["estimate"] is True
    # Day 1 末尾（六地藏簇 b2）→ Day 2 开头（宇治中心簇 a1）
    first = connectors[0]
    assert (first["from_id"], first["to_id"]) == ("b2", "a1")
    assert first["mode"] == "drive"  # 约 4km，按车程估算
    # 跨天段挂在出发天的 legs 末尾
    day1 = itinerary["days"][0]
    assert day1["legs"][-1]["cross_day"] is True


def test_规划进度事件经SSE发出(monkeypatch):
    monkeypatch.setattr(conversations, "SSE_IDLE_TIMEOUT", 0.2)
    client = make_client(FakeSeichiRepository(seichi=FIXTURE))
    conversation_id = create_conversation(client)
    post_message(client, conversation_id, "宇治三天京吹")

    with client.stream("GET", f"/api/conversations/{conversation_id}/events") as response:
        body = response.read().decode()
    items = [
        json.loads(line[len("data:"):].strip())
        for line in body.splitlines()
        if line.startswith("data:")
    ]

    stages = [item["data"]["stage"] for item in items if item["event"] == "planning"]
    assert stages == ["检索中", "聚类中", "排序中", "校验中", "讲解中", "完成"]
    assert items[-1]["event"] == "done"


def test_行程快照持久化_刷新可见():
    client = make_client(FakeSeichiRepository(seichi=FIXTURE))
    conversation_id = create_conversation(client)
    expected = post_message(client, conversation_id, "宇治三天京吹")["itinerary"]

    fresh_client = TestClient(app)
    response = fresh_client.get(f"/api/conversations/{conversation_id}/itinerary")

    assert response.status_code == 200
    assert response.json()["itinerary"] == expected

    # 历史消息的 payload 也带结构化行程
    history = fresh_client.get(f"/api/conversations/{conversation_id}/messages").json()
    assistant = [m for m in history if m["role"] == "assistant"][-1]
    assert assistant["payload"]["plan_itinerary"]["day_count"] == DAYS


def test_无候选圣地时优雅回复():
    client = make_client(FakeSeichiRepository(seichi=[]))
    conversation_id = create_conversation(client)

    body = post_message(client, conversation_id, "宇治三天京吹")

    assert body["reply"] == "三天行程已生成"
    assert body["itinerary"] is None
    assert body["seichi"] == []


def test_重新规划失败后旧快照不复活():
    """先成功规划出快照，再把数据源换空重规划：GET /itinerary 不得返回旧快照。"""
    repo = FakeSeichiRepository(seichi=FIXTURE)
    gateway = FakeLLMGateway(scripted=plan_script() + plan_script())
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    app.dependency_overrides[get_seichi_repository] = lambda: repo
    client = TestClient(app)
    conversation_id = create_conversation(client)
    assert post_message(client, conversation_id, "宇治三天京吹")["itinerary"] is not None

    # 第二轮：数据源变空，重规划失败
    empty_repo = FakeSeichiRepository(seichi=[])
    app.dependency_overrides[get_seichi_repository] = lambda: empty_repo
    body = post_message(client, conversation_id, "宇治三天京吹")
    assert body["itinerary"] is None

    fresh_client = TestClient(app)
    assert fresh_client.get(f"/api/conversations/{conversation_id}/itinerary").json()["itinerary"] is None


@pytest.mark.parametrize("text", ["宇治三天京吹", "宇治3天京吹", "京吹 三天"])
def test_启发式fake_LLM识别天数请求触发规划(text):
    """dev 演示路径：不 scripted 的 FakeLLMGateway 识别“N 天”+作品关键词。"""
    repo = FakeSeichiRepository(seichi=FIXTURE)
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway()  # 无脚本
    app.dependency_overrides[get_seichi_repository] = lambda: repo
    client = TestClient(app)
    conversation_id = create_conversation(client)

    body = post_message(client, conversation_id, text)

    assert body["itinerary"]["day_count"] == DAYS
    assert "3 天" in body["reply"] or "三天" in body["reply"]
