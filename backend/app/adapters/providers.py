"""Adapter wiring：按 settings 选择 fake 或 live 实现。

LLM/Transit 由 MEGURI_ADAPTER_MODE 控制；圣地数据源由独立的
MEGURI_SEICHI_MODE 控制（#4，与 LLM 解耦——真实 LLM 尚未接入，圣地检索
已可直连 anitabi）。这是唯一消费这些配置的地方；其余代码依赖端口
（app.adapters.ports），测试在 HTTP 缝 override 这些 provider。
"""

from app.adapters.anitabi import AnitabiSeichiRepository
from app.adapters.fakes import (
    FakeLLMGateway,
    FakeOpeningHours,
    FakeSeichiRepository,
    FakeTransitClient,
)
from app.adapters.file_seichi import FileSeichiRepository
from app.adapters.otp import OTPTransitClient
from app.adapters.overpass import OverpassOpeningHours
from app.adapters.ports import (
    CorpusStore,
    LLMGateway,
    OpeningHoursSource,
    SeichiRepository,
    TransitClient,
)
from app.config import get_settings
from app.rag.embedding import HashEmbeddingProvider, OpenAIEmbeddingProvider
from app.rag.store import InMemoryCorpusStore, PgVectorCorpusStore


def _live_not_available(name: str) -> None:
    raise NotImplementedError(f"live adapter for {name} arrives with a later ticket")


def get_llm_gateway() -> LLMGateway:
    if get_settings().adapter_mode == "fake":
        return FakeLLMGateway()
    _live_not_available("LLMGateway")


def get_seichi_repository() -> SeichiRepository:
    settings = get_settings()
    if settings.seichi_mode == "live":
        return AnitabiSeichiRepository()
    if settings.seichi_mode == "file":
        # 离线数据包（真实 anitabi 切片），anitabi 网络不可达时的 demo 模式
        return FileSeichiRepository(settings.seichi_data_dir)
    return FakeSeichiRepository()


def get_transit_client() -> TransitClient:
    settings = get_settings()
    if settings.transit_mode == "live":
        return OTPTransitClient(settings.otp_base_url)
    return FakeTransitClient()


def get_opening_hours_source() -> OpeningHoursSource:
    settings = get_settings()
    if settings.hours_mode == "live":
        return OverpassOpeningHours(settings.overpass_url)
    return FakeOpeningHours()


def get_corpus_store() -> CorpusStore:
    """RAG 统一检索接口（#8）：live = pgvector + embedding；fake = 内存。"""
    settings = get_settings()
    if settings.corpus_mode == "live":
        if settings.openai_api_key:
            embedder = OpenAIEmbeddingProvider(
                settings.openai_base_url, settings.openai_api_key, settings.embedding_model
            )
        else:
            # 无 key：确定性哈希向量——检索基础设施（pgvector/余弦/top-k）真实，
            # 向量是 fake；接真实 key 后自动切换（当前为 stub，见 ADR-0002）
            embedder = HashEmbeddingProvider(dim=settings.embedding_dim)
        from app.db import _get_engine

        return PgVectorCorpusStore(_get_engine(), embedder)
    return InMemoryCorpusStore()
