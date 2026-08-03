import json
import uuid

from fastapi.testclient import TestClient

from app.adapters.fakes import FakeLLMGateway
from app.adapters.providers import get_llm_gateway
from app.agents.events import event_bus
from app.agents.tools import ToolRegistry
from app.agents.tracing import InMemoryTracer
from app.api import conversations
from app.api.conversations import get_tool_registry, get_tracer
from app.main import app


def make_client(scripted: list[str] | None = None) -> TestClient:
    if scripted is not None:
        app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway(scripted=scripted)
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


def read_sse_events(response) -> list[str]:
    """读完整个 SSE 流，返回事件名序列。

    注意：starlette TestClient 会把响应体缓冲到流结束才交付，且无法在流
    打开期间并发处理第二个请求——所以测试里先把事件注入总线再一次性读流。
    """
    body = response.read().decode()
    return [
        json.loads(line[len("data:"):].strip())["event"]
        for line in body.splitlines()
        if line.startswith("data:")
    ]


def test_创建会话返回会话ID():
    client = make_client()

    conversation_id = create_conversation(client)

    assert uuid.UUID(conversation_id)  # 是合法的 UUID


def test_发送消息返回脚本化回复():
    client = make_client(scripted=["推荐你去丰乡小学校旧址"])
    conversation_id = create_conversation(client)

    body = post_message(client, conversation_id, "想巡礼轻音少女")

    assert body["reply"] == "推荐你去丰乡小学校旧址"


def test_读取会话返回完整对话历史_刷新不丢():
    client = make_client(scripted=["第一天的行程如下"])
    conversation_id = create_conversation(client)
    post_message(client, conversation_id, "帮我规划一天")

    # 换一个全新 client（模拟刷新页面后重新请求）
    fresh_client = TestClient(app)
    response = fresh_client.get(f"/api/conversations/{conversation_id}/messages")

    assert response.status_code == 200
    history = response.json()
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "帮我规划一天"),
        ("assistant", "第一天的行程如下"),
    ]


def test_回复过程推送进度事件(monkeypatch):
    monkeypatch.setattr(conversations, "SSE_IDLE_TIMEOUT", 0.2)
    client = make_client(scripted=["流式回复"])
    conversation_id = create_conversation(client)
    post_message(client, conversation_id, "触发事件")

    with client.stream("GET", f"/api/conversations/{conversation_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = read_sse_events(response)

    assert events == ["received", "thinking", "done"]


def test_SSE连接在done后保持开放_后续回合事件到达同一连接(monkeypatch):
    monkeypatch.setattr(conversations, "SSE_IDLE_TIMEOUT", 0.2)
    client = make_client(scripted=["回复一"])
    conversation_id = create_conversation(client)
    post_message(client, conversation_id, "第一条")

    # 第二回合事件（TestClient 无法在流打开期间并发处理第二个 POST，直接向
    # 总线注入；POST → 总线的链路已被其它测试覆盖）。若流在 done 后被服务端
    # 关闭，这些事件即使已在队列里也不会被推送出来。
    event_bus.publish(conversation_id, "received", {"text": "第二条"})
    event_bus.publish(conversation_id, "done", {"reply": "回复二"})

    with client.stream("GET", f"/api/conversations/{conversation_id}/events") as response:
        events = read_sse_events(response)

    assert events == ["received", "thinking", "done", "received", "done"]


def test_未知会话返回404():
    client = make_client()
    unknown_id = str(uuid.uuid4())

    post_response = client.post(
        f"/api/conversations/{unknown_id}/messages",
        json={"text": "在吗"},
    )
    get_response = client.get(f"/api/conversations/{unknown_id}/messages")

    assert post_response.status_code == 404
    assert get_response.status_code == 404


def test_畸形会话ID一律返回404():
    client = make_client()

    post_response = client.post(
        "/api/conversations/not-a-uuid/messages",
        json={"text": "在吗"},
    )
    get_response = client.get("/api/conversations/not-a-uuid/messages")
    events_response = client.get("/api/conversations/not-a-uuid/events")

    assert post_response.status_code == 404
    assert get_response.status_code == 404
    assert events_response.status_code == 404


class FakeSeichiTool:
    name = "search_seichi"
    description = "查询某作品在某区域的圣地（fake）"

    def run(self, args: dict) -> str:
        return "丰乡小学校旧址"


def make_react_client() -> tuple[TestClient, FakeLLMGateway, InMemoryTracer]:
    """脚本化 LLM：先请求工具调用，再给最终回复。"""
    gateway = FakeLLMGateway(
        scripted=[
            json.dumps({"type": "tool_call", "name": "search_seichi", "args": {"work": "轻音少女"}}),
            json.dumps({"type": "final", "content": "推荐你去丰乡小学校旧址"}),
        ]
    )
    registry = ToolRegistry()
    registry.register(FakeSeichiTool())
    tracer = InMemoryTracer()
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_tracer] = lambda: tracer
    return TestClient(app), gateway, tracer


def test_ReAct循环_执行工具后给出最终回复():
    client, gateway, _ = make_react_client()
    conversation_id = create_conversation(client)

    body = post_message(client, conversation_id, "想巡礼轻音少女")

    assert body["reply"] == "推荐你去丰乡小学校旧址"
    # 循环调了两次 LLM：tool_call 一轮、final 一轮
    assert len(gateway.calls) == 2
    # 工具观察结果（observation）进入了第二轮的 LLM 输入
    assert any("丰乡小学校旧址" in m["content"] for m in gateway.calls[1])


def test_循环过程写入trace事件():
    client, _, tracer = make_react_client()
    conversation_id = create_conversation(client)

    post_message(client, conversation_id, "想巡礼轻音少女")

    names = [e.name for e in tracer.events]
    assert names == ["loop_step", "llm_call", "tool_call", "loop_step", "llm_call"]
    tool_event = tracer.events[2]
    assert tool_event.payload["name"] == "search_seichi"
    assert "丰乡小学校旧址" in tool_event.payload["observation"]
    assert all(e.timestamp is not None for e in tracer.events)
