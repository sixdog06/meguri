"""LLMGateway 的 live 实现：经 LangChain 适配层调 chat 模型（ADR-0002）。

base_url / api_key / model 来自 settings（.env.local 注入，勿入库）。
不进 pytest（无网/key 依赖）；离线测试用 FakeLLMGateway。

故障语义：连接类错误（APIConnectionError/APITimeoutError）有限重试
（指数退避）；4xx 不重试。重试仍失败抛 LLMUnavailableError——
Orchestrator/API 层映射为 503，不炸 500。
"""

import time
from typing import Any, Callable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APITimeoutError


class LLMUnavailableError(Exception):
    """LLM 服务连接失败（重试后仍不可达）——API 层映射 503。"""


class LangChainLLMGateway:
    generative_capable = True  # 真实模型，可用于生成式讲解

    MAX_RETRIES = 2  # 连接类错误的重试次数（不含首次）
    RETRY_BASE_SECONDS = 2.0  # 指数退避基数：2s, 4s

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        temperature: float = 1.0,  # kimi-for-coding 只允许 1（其它模型可在调用处覆盖）
        timeout: float = 30.0,
    ) -> None:
        self._chat = ChatOpenAI(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            timeout=timeout,
        )

    def complete(
        self,
        messages: list[dict[str, str]],
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """调 chat 模型返回文本；on_chunk 非空时走 .stream() 逐段回调（真流式）。

        连接类错误重试后抛 LLMUnavailableError；流已开始（已有 chunk 上屏）
        后出错不重试（避免重复推送），直接抛。
        """
        converted = [self._convert(m) for m in messages]
        last_error: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            streamed_any = False
            try:
                if on_chunk is None:
                    response = self._chat.invoke(converted)
                    return self._content_text(response.content)
                parts: list[str] = []
                for chunk in self._chat.stream(converted):
                    text = self._content_text(chunk.content)
                    if not text:
                        continue
                    streamed_any = True
                    parts.append(text)
                    on_chunk(text)
                return "".join(parts)
            except (APIConnectionError, APITimeoutError) as exc:
                if streamed_any:  # 已有增量上屏，重试会重复推送——直接失败
                    raise LLMUnavailableError(str(exc)) from exc
                last_error = exc
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_BASE_SECONDS * (2**attempt))
        raise LLMUnavailableError(str(last_error)) from last_error

    @staticmethod
    def _content_text(content: Any) -> str:
        """LangChain content（str 或多段 list）拼成纯文本。"""
        if isinstance(content, list):  # 多段内容拼文本
            return "".join(str(part.get("text", "")) for part in content)
        return str(content)

    @staticmethod
    def _convert(message: dict[str, str]) -> BaseMessage:
        """自研 wire format 的消息 → LangChain 消息对象。"""
        role = message.get("role", "user")
        content = message.get("content", "")
        if role == "system":
            return SystemMessage(content=content)
        if role == "assistant":
            return AIMessage(content=content)
        if role == "tool":
            # 自研 wire format 的观察结果以用户消息形式回填（不依赖原生 tool calling）
            return HumanMessage(content=f"[工具观察结果] {content}")
        return HumanMessage(content=content)
