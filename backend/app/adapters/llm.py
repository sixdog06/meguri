"""LLMGateway 的 live 实现：经 LangChain 适配层调 chat 模型（ADR-0002）。

base_url / api_key / model 来自 settings（.env.local 注入，勿入库）。
不进 pytest（无网/key 依赖）；离线测试用 FakeLLMGateway。

故障语义：连接类错误（APIConnectionError/APITimeoutError）有限重试
（指数退避），重试仍失败抛 LLMUnavailableError；4xx（认证/请求错误，
如 AuthenticationError/BadRequestError）不重试，直接包装成
LLMUnavailableError——Orchestrator/API 层映射为 503，不炸 500。
"""

import threading
import time
from typing import Any, Callable

import httpx

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
)

# 4xx 客户端错误：key 失效/请求非法，重试无意义——包装成统一故障语义走 503
_CLIENT_ERRORS = (AuthenticationError, PermissionDeniedError, BadRequestError, NotFoundError)


class LLMUnavailableError(Exception):
    """LLM 服务不可用（连接失败重试后仍不可达，或 4xx 认证/请求错误）——API 层映射 503。"""


class _HardTimeoutError(Exception):
    """墙钟硬时限超时（httpx 超时是"空闲"语义，代理吊住响应头时永远不触发）。"""


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
        self._timeout = timeout
        self._chat = ChatOpenAI(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            timeout=timeout,
            # 关掉 SDK 内部重试（默认 2 次）：重试策略只在本网关层（MAX_RETRIES），
            # 否则每层讲解调用最坏 3×3×timeout，整站讲解链可拖到天荒地老
            max_retries=0,
            # trust_env=False：不读系统代理。实测（2026-08-27）本机系统代理
            # （Clash :7890）会把部分请求的 CONNECT 隧道吊死——响应头永远不到，
            # httpx 空闲超时也不触发；国内 API 直连更快更稳
            http_client=httpx.Client(trust_env=False, timeout=timeout),
        )

    def complete(
        self,
        messages: list[dict[str, str]],
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """调 chat 模型返回文本；on_chunk 非空时走 .stream() 逐段回调（真流式）。

        连接类错误重试后抛 LLMUnavailableError；流已开始（已有 chunk 上屏）
        后出错不重试（避免重复推送），直接抛。4xx 认证/请求错误不重试，
        同样包装成 LLMUnavailableError。
        """
        converted = [self._convert(m) for m in messages]
        last_error: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            streamed_any = False
            try:
                if on_chunk is None:
                    response = self._call_with_deadline(converted)
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
            except _CLIENT_ERRORS as exc:
                # 4xx 是认证/请求问题而非连接故障，重试无意义——直接包装
                raise LLMUnavailableError(
                    f"LLM 请求被拒绝（{type(exc).__name__}）: {exc}"
                ) from exc
            except (APIConnectionError, APITimeoutError, _HardTimeoutError) as exc:
                if streamed_any:  # 已有增量上屏，重试会重复推送——直接失败
                    raise LLMUnavailableError(str(exc)) from exc
                last_error = exc
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_BASE_SECONDS * (2**attempt))
        raise LLMUnavailableError(str(last_error)) from last_error

    HARD_DEADLINE_GRACE = 5.0  # 硬时限 = timeout + 宽限（等 httpx 自己先超时）

    def _call_with_deadline(self, converted: list[BaseMessage]) -> Any:
        """invoke 加墙钟硬时限：httpx 的 read timeout 是"收不到字节的空闲"
        语义——代理把 CONNECT 隧道吊住（一个字节都不回）时实测 >300s 不触发
        （2026-08-27 经本机代理调 kimi 复现）。守护线程 + join 硬等：超时即
        遗弃该线程（daemon，不拖进程退出；泄漏的 socket 由 OS 回收），按
        连接类故障走既有重试/降级策略。
        """
        outcome: dict[str, Any] = {}

        def target() -> None:
            try:
                outcome["value"] = self._chat.invoke(converted)
            except BaseException as exc:  # 带回主线程重抛，走统一异常分类
                outcome["error"] = exc

        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        worker.join(self._timeout + self.HARD_DEADLINE_GRACE)
        if worker.is_alive():
            raise _HardTimeoutError(
                f"LLM 调用超过硬时限 {self._timeout + self.HARD_DEADLINE_GRACE:.0f}s"
            )
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]

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
