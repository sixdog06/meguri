"""Ports for external services.

Every external dependency (LLM provider, seichi data source, transit engine)
is accessed through one of these protocols. Tests and local development wire
fakes (app.adapters.fakes); live implementations arrive with later tickets.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol


@dataclass
class Seichi:
    """候选圣地：名称、坐标、对照截图引用、出处（集数+截图来源）。

    SeichiRepository 端口上的统一数据类型——fake 与 live（anitabi）适配器
    都返回它，API 边界的 schema 从它序列化，字段名不再泄漏数据源原始命名
    （anitabi 的 `s` 在这里是 `ep_seconds`）。
    """

    name: str
    lat: float
    lng: float
    id: str | None = None
    work: str | None = None
    area: str | None = None
    image: str | None = None  # 对照截图（缩略图 URL）
    ep: int | str | None = None  # 出处集数（可能为 "OST" 等）
    ep_seconds: int | None = None  # 截图在集内的时间（秒）
    origin: str | None = None  # 截图来源（CC BY-NC-SA 要求标注）
    origin_url: str | None = None


@dataclass
class WorkRef:
    """作品解析结果（作品名 → bangumi subjectID + 巡礼主城市）。"""

    subject_id: int  # bangumi subjectID（anitabi 的作品 id 同体系）
    name: str  # 中文名优先
    city: str


class LLMGateway(Protocol):
    #: 能力标志：是否可用于生成式讲解（真实模型 True；fake 为 False——
    #: fake 的 scripted 输出要留给 ReAct 循环，不能喂生成调用）。wiring 统一读取。
    generative_capable: bool

    def complete(
        self,
        messages: list[dict[str, str]],
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """返回完整文本；on_chunk 非空时边生成边回调增量文本（真流式）。

        实现方注意：on_chunk 只在该回调路径上保证逐字推送；调用方负责判断
        输出形态（Orchestrator 对 JSON 工具调用会缓冲不上屏）。
        """
        ...


class SeichiRepository(Protocol):
    def search_seichi(self, work: str, area: str) -> list[Seichi]: ...

    def find_work(self, work: str) -> "WorkRef | None":
        """作品名 → 作品解析（subjectID/名称/主城市）；找不到返回 None。

        语料灌库等场景经此公开方法取作品标识，不得绕过端口直调数据源。"""
        ...


class TransitClient(Protocol):
    """交通查询端口（ADR-0003：OTP）。

    返回 dict 契约：{"mode", "duration_minutes", "fare_yen", "estimate"}——
    estimate=True 表示"没有真实数据"（fake/降级），调用方保留原估算段；
    estimate=False 表示真实查询结果，替换估算段。异常上抛由调用方降级；
    返回 None 表示熔断打开（OTP 故障 TTL 内不再打网），同样保留估算段。
    """

    def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        *,
        depart_at: datetime | None = None,
    ) -> dict[str, Any] | None: ...


@dataclass
class CorpusChunk:
    """RAG 语料块（#8）：作品条目 / 地标描述，经统一检索接口访问。"""

    id: str  # 稳定 id（如 bangumi:115908 / anitabi:115908:7gs3o1mm），upsert 幂等靠它
    source: str  # 语料来源（bangumi.tv / anitabi）
    work: str
    text: str


class EmbeddingProvider(Protocol):
    """文本向量化端口。无真实 key 时用确定性哈希向量（fake）开发/测试。"""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class CorpusStore(Protocol):
    """统一检索接口（#8）：语料写入与 top-k 相似检索。"""

    def upsert(self, chunks: list[CorpusChunk]) -> None: ...

    def search(self, query: str, k: int, work: str | None = None) -> list[CorpusChunk]:
        """top-k 相似检索；work 非空时只返回该作品的语料——讲解必须出自
        当前作品，跨作品"同名地点"的语料不许错配（零幻觉底线的边界）。"""
        ...
