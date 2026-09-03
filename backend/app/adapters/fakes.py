"""适配器端口的确定性测试替身：经 HTTP 缝 dependency override 注入，
不走生产装配（providers 只产真实实现）。
"""

from typing import Any, Callable

from app.adapters.ports import Seichi


class FakeLLMGateway:
    """脚本化 LLM 替身：scripted 按序弹出；脚本耗尽返回固定文本。

    calls 记录每次 complete 的完整入参，供测试断言 prompt/循环行为。
    """

    generative_capable = False  # scripted 输出只喂 ReAct 循环（见 LLMGateway 协议）

    def __init__(self, scripted: list[str] | None = None) -> None:
        self._scripted = list(scripted or [])
        self.calls: list[list[dict[str, str]]] = []  # 每次 complete 收到的消息，供测试断言

    def complete(
        self,
        messages: list[dict[str, str]],
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """LLMGateway 契约：返回 wire format JSON 或纯文本（由编排层解析）。

        fake 不做逐字流（一次性返回），on_chunk 仅满足签名——流式行为由
        LangChainLLMGateway 与 Orchestrator 的单测覆盖。
        """
        self.calls.append(messages)
        if self._scripted:
            return self._scripted.pop(0)
        return "fake-llm-response"


class FakeSeichiRepository:
    """固定数据集的圣地仓库 fake；calls 记录检索入参供测试断言走端口。"""

    def __init__(self, seichi: list[Seichi] | None = None) -> None:
        self._seichi = list(seichi or [])
        self.calls: list[tuple[str, str]] = []  # 每次 search_seichi 的 (work, area)，供测试断言

    def search_seichi(self, work: str, area: str) -> list[Seichi]:
        """按作品精确匹配 + 地区宽松匹配（与 live 实现语义一致）过滤固定数据集。"""
        self.calls.append((work, area))
        # 地区宽松匹配，与 live 实现（anitabi 城市名）语义一致
        return [
            s
            for s in self._seichi
            if s.work == work and (not area or area in (s.area or "") or (s.area or "") in area)
        ]


class FakeTransitClient:
    """默认 fake：estimate=True 表示"没有真实数据"，Navigator 保留原估算段。

    scripted 提供时按序返回（模拟真实查询结果，供 Navigator 测试）。
    """

    def __init__(self, scripted: list[dict[str, Any]] | None = None) -> None:
        self._scripted = list(scripted or [])
        self.calls: list[tuple] = []  # 每次 route 的 (origin, destination, depart_at)

    def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        *,
        depart_at: Any = None,
    ) -> dict[str, Any]:
        """TransitClient 契约：scripted 按序弹出；否则返回 estimate=True 占位。"""
        self.calls.append((origin, destination, depart_at))
        if self._scripted:
            return self._scripted.pop(0)
        return {"mode": "fake", "duration_minutes": 0, "fare_yen": None, "estimate": True}
