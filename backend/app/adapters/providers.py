"""Adapter wiring：按 settings 选择 live / file 实现。

生产只装配真实适配器（fake 体系仅供测试，经 HTTP 缝 dependency override
注入，不走这里）。seichi_mode 是唯一保留的模式开关；其余代码依赖端口
（app.adapters.ports），测试 override 对应 provider 即换测试替身。
"""

from app.adapters.anitabi import AnitabiClient, AnitabiSeichiRepository
from app.adapters.file_seichi import FileSeichiRepository
from app.adapters.llm import LangChainLLMGateway
from app.adapters.otp import OTPTransitClient
from app.adapters.ports import (
    LLMGateway,
    SeichiRepository,
    TransitClient,
)
from app.config import get_settings


def get_llm_gateway() -> LLMGateway:
    """LLM 网关唯一实现：LangChain 真实模型（缺 key 明确报错）。"""
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError(
            "LLM 网关需要 MEGURI_OPENAI_API_KEY（写入 .env.local，勿入库）"
        )
    return LangChainLLMGateway(
        settings.openai_base_url, settings.openai_api_key, settings.openai_model
    )


def generative_llm(llm: LLMGateway) -> LLMGateway | None:
    """生成式讲解用的 LLM 筛选（能力标志，见 LLMGateway 协议）：真实模型返回
    本身，无生成能力的测试替身返回 None（保持检索式拼装讲解）。"""
    return llm if getattr(llm, "generative_capable", False) else None


def get_seichi_repository() -> SeichiRepository:
    """按 seichi_mode 选圣地数据源：live=本地映射+anitabi 实时（故障显式 503 /
    本地包兜底），file=纯离线数据包（映射+离线切片）。"""
    settings = get_settings()
    if settings.seichi_mode == "file":
        # 纯离线数据包（真实 anitabi 切片），完全不触网时用
        return FileSeichiRepository(settings.seichi_data_dir)
    from app.adapters.works_db import DbWorksResolver
    from app.db import _get_engine

    return AnitabiSeichiRepository(
        mapping=FileSeichiRepository(settings.seichi_data_dir),
        # debug_mode=true：anitabi 不触网，返回罐头数据（开发用）
        client=AnitabiClient(debug=settings.debug_mode),
        # 作品名解析走 anime_works 表（pg_trgm）；数据来自 ingest_works 灌库
        resolver=DbWorksResolver(_get_engine()),
    )


def get_transit_client() -> TransitClient:
    """交通查询唯一实现：本地 OTP（GraphQL）；不可达时 Navigator 降级估算。"""
    return OTPTransitClient(get_settings().otp_base_url)
