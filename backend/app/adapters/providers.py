"""Adapter wiring：按 settings 选择 fake / live / file 实现。

LLM 由 MEGURI_ADAPTER_MODE 控制；圣地数据源由独立的 MEGURI_SEICHI_MODE
控制（#4，与 LLM 解耦）；交通/语料库各有 *_mode 开关。这是唯一
消费这些配置的地方；其余代码依赖端口（app.adapters.ports），测试在
HTTP 缝 override 这些 provider。
"""

from app.adapters.anitabi import AnitabiClient, AnitabiSeichiRepository
from app.adapters.fakes import (
    FakeLLMGateway,
    FakeSeichiRepository,
    FakeTransitClient,
)
from app.adapters.file_seichi import FileSeichiRepository
from app.adapters.llm import LangChainLLMGateway
from app.adapters.otp import OTPTransitClient
from app.adapters.ports import (
    CorpusStore,
    EmbeddingProvider,
    LLMGateway,
    SeichiRepository,
    TransitClient,
)
from app.config import get_settings
from app.rag.embedding import HashEmbeddingProvider, OpenAIEmbeddingProvider
from app.rag.store import InMemoryCorpusStore, PgVectorCorpusStore


def _live_not_available(name: str) -> None:
    raise NotImplementedError(f"live adapter for {name} arrives with a later ticket")


def get_llm_gateway() -> LLMGateway:
    """按 adapter_mode 选 LLM 网关：live=LangChain 真实模型（缺 key 明确报错），fake=脚本化。"""
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
    """按 seichi_mode 选圣地数据源：live=本地映射+anitabi 实时（故障显式 503，
    不降级本地数据包），file=纯离线数据包（映射+离线切片），fake=内存。"""
    settings = get_settings()
    if settings.seichi_mode == "live":
        return AnitabiSeichiRepository(
            mapping=FileSeichiRepository(settings.seichi_data_dir),
            # debug_mode=true：anitabi 不触网，返回罐头数据（开发用）
            client=AnitabiClient(debug=settings.debug_mode),
        )
    if settings.seichi_mode == "file":
        # 纯离线数据包（真实 anitabi 切片），完全不触网时用
        return FileSeichiRepository(settings.seichi_data_dir)
    return FakeSeichiRepository()


def get_transit_client() -> TransitClient:
    """按 transit_mode 选交通查询：live=本地 OTP（GraphQL），fake=估算占位。"""
    settings = get_settings()
    if settings.transit_mode == "live":
        return OTPTransitClient(settings.otp_base_url)
    return FakeTransitClient()


def get_corpus_store() -> CorpusStore:
    """RAG 统一检索接口（#8）：live = pgvector + embedding；fake = 内存。"""
    settings = get_settings()
    if settings.corpus_mode == "live":
        # embedding：有 key 用 OpenAI 兼容真向量（独立 embedding_base_url/
        # embedding_api_key 优先，缺省回退 chat LLM 的 openai_*；dimensions
        # 对齐 embedding_dim，维度不符/调用失败明确报错，不降级）；无 key 用
        # 确定性哈希向量——检索基础设施真实，向量是 fake。
        api_key = settings.embedding_api_key or settings.openai_api_key
        if api_key:
            embedder: EmbeddingProvider = OpenAIEmbeddingProvider(
                base_url=settings.embedding_base_url or settings.openai_base_url,
                api_key=api_key,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
            )
        else:
            embedder = HashEmbeddingProvider(dim=settings.embedding_dim)
        from app.db import _get_engine

        return PgVectorCorpusStore(
            _get_engine(), embedder, min_score=settings.corpus_min_score
        )
    return InMemoryCorpusStore()
