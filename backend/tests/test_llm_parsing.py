"""真实 LLM 适配的离线测试（不触网）：健壮解析 + system prompt 动态工具清单。"""

import json

from app.adapters.fakes import FakeLLMGateway
from app.agents.orchestrator import _parse_llm_output, _system_prompt
from app.agents.tools import PlanItineraryTool, SearchSeichiTool, ToolRegistry


def test_解析带markdown_fence的JSON():
    raw = '```json\n{"type": "final", "content": "好的"}\n```'
    assert _parse_llm_output(raw) == {"type": "final", "content": "好的"}


def test_解析带前后散文的JSON():
    raw = '我来调用工具：\n{"type": "tool_call", "name": "search_seichi", "args": {"work": "w"}}\n请稍等'
    result = _parse_llm_output(raw)
    assert result["type"] == "tool_call"
    assert result["name"] == "search_seichi"


def test_解析纯文本兜底为final():
    assert _parse_llm_output("今天天气不错") == {"type": "final", "content": "今天天气不错"}


def test_解析未闭合的final_JSON提取content():
    """真实模型偶发未闭合/截断的 final JSON（曾整串上屏）：抢救 content。"""
    raw = '{"type": "final", "content": "已帮你检索到《轻音少女》。\\n\\n## 第1天：修学院'
    assert _parse_llm_output(raw) == {
        "type": "final",
        "content": "已帮你检索到《轻音少女》。\n\n## 第1天：修学院",
    }


def test_system_prompt含动态工具清单():
    registry = ToolRegistry()
    registry.register(SearchSeichiTool(repository=None))
    registry.register(PlanItineraryTool(repository=None))

    prompt = _system_prompt(registry)

    assert "search_seichi" in prompt
    assert "plan_itinerary" in prompt
    assert "days" in prompt  # args_hint 进 prompt
    assert "tool_call" in prompt and "最终回复" in prompt  # 线格式说明（final 为纯文本正文）


def test_循环首条消息是system_prompt():
    """Orchestrator 每轮把 system prompt 放在对话历史之前喂给网关。"""
    from fastapi.testclient import TestClient

    from app.adapters.providers import get_llm_gateway
    from app.main import app

    gateway = FakeLLMGateway(scripted=["回复"])
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    client = TestClient(app)
    cid = client.post("/api/conversations").json()["conversation_id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": "你好"})

    first_call = gateway.calls[0]
    assert first_call[0]["role"] == "system"
    assert "Meguri" in first_call[0]["content"]
    assert first_call[1]["role"] == "user"
