"""Ticket #4：Scout 圣地检索。

行为测试经 HTTP 缝驱动；anitabi 客户端以固定数据集的 FakeSeichiRepository
替换（dependency_overrides 注入到 SeichiRepository 端口）。
"""

import json

from fastapi.testclient import TestClient

from app.adapters.fakes import FakeLLMGateway, FakeSeichiRepository
from app.adapters.ports import Seichi
from app.adapters.providers import get_llm_gateway, get_seichi_repository
from app.main import app

WORK = "吹响吧！上低音号"
AREA = "宇治"

SEICHI_FIXTURE = [
    Seichi(
        id="7gs3o1mm",
        name="宇治桥",
        work=WORK,
        area="宇治市",
        lat=34.8929,
        lng=135.8065,
        image="https://image.anitabi.cn/points/115908/7gs3o1mm.jpg?plan=h160",
        ep=2,
        ep_seconds=809,
        origin="Anitabi@卜卜口",
        origin_url="https://anitabi.cn/",
    ),
    Seichi(
        id="qys7k4",
        name="大吉山展望台 蓝调",
        work=WORK,
        area="宇治市",
        lat=34.8926,
        lng=135.8125,
        image="https://image.anitabi.cn/user/0/bangumi/115908/points/qys7k4.jpg?plan=h160",
        ep=8,
        ep_seconds=1131,
        origin="Google Maps",
        origin_url="https://www.google.com/maps/d/viewer?mid=13mgdlajJV0HxpqKf6ri2NnEHFBc",
    ),
    # 其它作品/地区的数据：不应出现在检索结果里
    Seichi(
        id="other",
        name="丰乡小学校旧址",
        work="轻音少女",
        area="丰乡町",
        lat=35.0,
        lng=136.0,
        ep=1,
        ep_seconds=100,
        origin="fake",
    ),
]


def make_client(repo: FakeSeichiRepository) -> TestClient:
    """脚本化 LLM 走 search_seichi 工具；repo 经端口注入（不 override 工具注册表，

    这样生产 wiring（get_tool_registry → SearchSeichiTool）也被覆盖到）。"""
    gateway = FakeLLMGateway(
        scripted=[
            json.dumps(
                {"type": "tool_call", "name": "search_seichi", "args": {"work": WORK, "area": AREA}}
            ),
            json.dumps({"type": "final", "content": "为你找到了候选圣地"}),
        ]
    )
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


def test_按作品城市检索返回结构化候选圣地列表():
    repo = FakeSeichiRepository(seichi=SEICHI_FIXTURE)
    client = make_client(repo)
    conversation_id = create_conversation(client)

    body = post_message(client, conversation_id, "宇治有哪些京吹的圣地")

    assert body["reply"] == "为你找到了候选圣地"
    seichi = body["seichi"]
    assert [s["name"] for s in seichi] == ["宇治桥", "大吉山展望台 蓝调"]
    first = seichi[0]
    # 名称、坐标、对照截图引用、出处（集数+来源）
    assert first["lat"] == 34.8929
    assert first["lng"] == 135.8065
    assert first["image"].startswith("https://image.anitabi.cn/points/115908/")
    assert first["ep"] == 2
    assert first["ep_seconds"] == 809
    assert first["origin"] == "Anitabi@卜卜口"


def test_圣地数据经repository端口获取():
    repo = FakeSeichiRepository(seichi=SEICHI_FIXTURE)
    client = make_client(repo)
    conversation_id = create_conversation(client)

    post_message(client, conversation_id, "宇治有哪些京吹的圣地")

    # 断言检索确实经由 SeichiRepository 端口，且参数为作品+城市
    assert repo.calls == [(WORK, AREA)]


def test_候选圣地随assistant消息持久化_刷新历史可见():
    repo = FakeSeichiRepository(seichi=SEICHI_FIXTURE)
    client = make_client(repo)
    conversation_id = create_conversation(client)
    post_message(client, conversation_id, "宇治有哪些京吹的圣地")

    fresh_client = TestClient(app)
    response = fresh_client.get(f"/api/conversations/{conversation_id}/messages")

    assert response.status_code == 200
    history = response.json()
    assistant = [m for m in history if m["role"] == "assistant"][-1]
    # 结构化结果按工具名作为 payload 键收集
    assert [s["name"] for s in assistant["payload"]["search_seichi"]] == [
        "宇治桥",
        "大吉山展望台 蓝调",
    ]
    user = [m for m in history if m["role"] == "user"][-1]
    assert user["payload"] is None


def test_检索结果为空时优雅回复():
    repo = FakeSeichiRepository(seichi=[])
    client = make_client(repo)
    conversation_id = create_conversation(client)

    body = post_message(client, conversation_id, "宇治有哪些京吹的圣地")

    assert body["reply"] == "为你找到了候选圣地"
    assert body["seichi"] == []
