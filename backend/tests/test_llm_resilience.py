"""LLM 连接故障的优雅降级：重试 → 503 + 友好 detail，不留脏数据。"""

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from app.adapters.llm import LangChainLLMGateway, LLMUnavailableError
from app.adapters.providers import get_llm_gateway
from app.main import app


def _conn_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", "https://api.kimi.com/coding/v1/chat/completions"))


def _bad_request() -> openai.BadRequestError:
    request = httpx.Request("POST", "https://api.kimi.com/coding/v1/chat/completions")
    return openai.BadRequestError(
        "invalid temperature", response=httpx.Response(400, request=request), body=None
    )


def _auth_error() -> openai.AuthenticationError:
    request = httpx.Request("POST", "https://api.kimi.com/coding/v1/chat/completions")
    return openai.AuthenticationError(
        "invalid api key", response=httpx.Response(401, request=request), body=None
    )


class _FailingChat:
    def __init__(self, error_factory, results=None, fail_times=0):
        self._error_factory = error_factory
        self._results = list(results or [])
        self._fail_times = fail_times  # 前 N 次调用抛错，之后弹 results
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._error_factory()
        if self._results:
            return self._results.pop(0)
        raise self._error_factory()


class _OkResponse:
    content = "ok"


def make_gateway(chat, monkeypatch) -> LangChainLLMGateway:
    monkeypatch.setattr("time.sleep", lambda seconds: None)  # 退避不在测试里真睡
    gateway = LangChainLLMGateway("http://x", "k", "m")
    gateway._chat = chat
    return gateway


def test_连接错误重试后仍失败_抛LLMUnavailable(monkeypatch):
    chat = _FailingChat(_conn_error)
    gateway = make_gateway(chat, monkeypatch)

    with pytest.raises(LLMUnavailableError):
        gateway.complete([{"role": "user", "content": "hi"}])

    assert chat.calls == 3  # 首次 + 2 次重试


def test_重试中途成功则正常返回(monkeypatch):
    chat = _FailingChat(_conn_error, results=[_OkResponse()], fail_times=1)
    gateway = make_gateway(chat, monkeypatch)

    # 第一次抛连接错误，重试第二次成功
    assert gateway.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert chat.calls == 2


def test_4xx不重试_包装为LLMUnavailable(monkeypatch):
    chat = _FailingChat(_bad_request)
    gateway = make_gateway(chat, monkeypatch)

    with pytest.raises(LLMUnavailableError):
        gateway.complete([{"role": "user", "content": "hi"}])

    assert chat.calls == 1  # 4xx 是请求问题，重试无意义


def test_认证错误包装为LLMUnavailable且注明非连接故障(monkeypatch):
    """key 失效等 4xx 穿透会炸 500——包装成 LLMUnavailableError 复用 503 路径。"""
    chat = _FailingChat(_auth_error)
    gateway = make_gateway(chat, monkeypatch)

    with pytest.raises(LLMUnavailableError, match="AuthenticationError"):
        gateway.complete([{"role": "user", "content": "hi"}])

    assert chat.calls == 1


# --- HTTP 缝：重试仍失败 → 503 + 友好 detail + 无脏数据 ---


class FailingLLMGateway:
    generative_capable = False

    def __init__(self):
        self.calls = 0

    def complete(self, messages, on_chunk=None):
        self.calls += 1
        raise LLMUnavailableError("connection error")


def test_LLM不可达返回503且不留脏assistant消息(monkeypatch):
    from app.api import conversations

    monkeypatch.setattr(conversations, "SSE_IDLE_TIMEOUT", 0.2)
    gateway = FailingLLMGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    client = TestClient(app)
    cid = client.post("/api/conversations").json()["conversation_id"]

    response = client.post(
        f"/api/conversations/{cid}/messages", json={"text": "宇治三天京吹"}
    )

    assert response.status_code == 503
    assert "模型服务" in response.json()["detail"]

    # assistant 侧无脏数据：只有用户消息落了库
    history = client.get(f"/api/conversations/{cid}/messages").json()
    assert [(m["role"]) for m in history] == ["user"]

    # SSE 推送了 error 事件（进度通道如实告知失败）
    with client.stream("GET", f"/api/conversations/{cid}/events") as sse:
        body = sse.read().decode()
    events = [line for line in body.splitlines() if '"event"' in line]
    assert any("error" in line for line in events)


def test_LLM不可达不影响后续正常请求(monkeypatch):
    gateway = FailingLLMGateway()
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    client = TestClient(app)
    cid = client.post("/api/conversations").json()["conversation_id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": "hi"})

    from app.adapters.fakes import FakeLLMGateway

    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway(scripted=["恢复了"])
    response = client.post(f"/api/conversations/{cid}/messages", json={"text": "再来"})

    assert response.status_code == 200
    assert response.json()["reply"] == "恢复了"
