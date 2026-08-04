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
from app.adapters.llm import LangChainLLMGateway
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
from app.rag.embedding import HashEmbeddingProvider
from app.rag.store import InMemoryCorpusStore, PgVectorCorpusStore


def _live_not_available(name: str) -> None:
    raise NotImplementedError(f"live adapter for {name} arrives with a later ticket")


def get_llm_gateway() -> LLMGateway:
    settings = get_settings()
    if settings.adapter_mode == "live":
        if not settings.openai_api_key:
            raise RuntimeError(
                "adapter_mode=live 需要 MEGURI_OPENAI_API_KEY（写入 .env.local，勿入库）"
            )
        return LangChainLLMGateway(
            settings.openai_base_url, settings.openai_api_key, settings.openai_model
        )
    return FakeLLMGateway()


def generative_llm(llm: LLMGateway) -> LLMGateway | None:
    """生成式讲解用的 LLM 筛选（能力标志，见 LLMGateway 协议）：真实模型返回
    本身，fake 返回 None（其 scripted 输出只喂 ReAct 循环）。"""
    return llm if getattr(llm, "generative_capable", False) else None


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
        # embedding：OpenAI 兼容 provider 还是 stub（ADR-0002 待 LangChain
        # 落地），此前一律用确定性哈希向量——检索基础设施（pgvector/余弦/
        # top-k）真实，向量是 fake；chat key 只用于 LLM 网关，与这里无关。
        embedder = HashEmbeddingProvider(dim=settings.embedding_dim)
        from app.db import _get_engine

        return PgVectorCorpusStore(_get_engine(), embedder)
    return InMemoryCorpusStore()
