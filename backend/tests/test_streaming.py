"""真流式（逐字上屏）：LLM 边生成边经 SSE reply_chunk 推送。

协议（orchestrator 模块头）：最终回复是纯文本正文 → 逐段推送；
JSON（工具调用/旧版 final 包装）按首个非空白字符识别 → 缓冲不上屏。
离线：stub 网关同步喂 chunks，事件从共享 event_bus 订阅队列收集。
"""

import testsupport  # noqa: F401

from fastapi.testclient import TestClient

from app.adapters.providers import get_llm_gateway
from app.agents.events import event_bus
from app.main import app


class ScriptedStreamGateway:
    """模拟真模型的流式网关：每轮按预定 chunks 回调 on_chunk，返回全文。"""

    generative_capable = False

    def __init__(self, scripts: list[list[str]]) -> None:
        self._scripts = list(scripts)

    def complete(self, messages, on_chunk=None):
        chunks = self._scripts.pop(0)
        if on_chunk:
            for chunk in chunks:
                on_chunk(chunk)
        return "".join(chunks)


def _post_collect_events(client: TestClient, cid: str, text: str) -> tuple[dict, list[dict]]:
    """发一条消息并收集该会话的全部 SSE 事件（同步处理完即齐全）。"""
    queue = event_bus.subscribe(cid)
    response = client.post(f"/api/conversations/{cid}/messages", json={"text": text})
    events = []
    while not queue.empty():
        events.append(queue.get())
    return response.json(), events


def test_纯文本final逐段推送():
    gateway = ScriptedStreamGateway([["京都", "有很多", "圣地。"]])
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    client = TestClient(app)
    cid = client.post("/api/conversations").json()["conversation_id"]

    body, events = _post_collect_events(client, cid, "介绍一下")

    chunks = [e["data"]["text"] for e in events if e["event"] == "reply_chunk"]
    assert chunks == ["京都", "有很多", "圣地。"]  # 逐段、按序
    assert body["reply"] == "京都有很多圣地。"
    assert any(e["event"] == "done" for e in events)


def test_工具调用JSON缓冲不上屏():
    """首轮输出 JSON 工具调用（甚至被拆成多段）不推 reply_chunk；次轮正文才推。"""
    gateway = ScriptedStreamGateway([
        ['{"type": "tool', '_call", "name": "search_seichi", "args": {"ani_name": "京吹"}}'],
        ["查到了", "，共 8 处。"],
    ])
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    client = TestClient(app)
    cid = client.post("/api/conversations").json()["conversation_id"]

    body, events = _post_collect_events(client, cid, "宇治京吹")

    chunks = [e["data"]["text"] for e in events if e["event"] == "reply_chunk"]
    assert chunks == ["查到了", "，共 8 处。"]
    assert not any("tool_call" in c for c in chunks)
    assert body["reply"] == "查到了，共 8 处。"


def test_旧版JSON_final缓冲但内容仍提取():
    """模型不遵新协议、仍输出 JSON final：不上屏原始 JSON，回复取 content。"""
    gateway = ScriptedStreamGateway([['{"type": "final", "content": "旧格式回复"}']])
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    client = TestClient(app)
    cid = client.post("/api/conversations").json()["conversation_id"]

    body, events = _post_collect_events(client, cid, "你好")

    assert [e for e in events if e["event"] == "reply_chunk"] == []
    assert body["reply"] == "旧格式回复"
