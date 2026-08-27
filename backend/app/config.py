"""应用配置：全部运行时开关集中于此（MEGURI_ 环境变量前缀 + .env.local）。

各 *_mode 开关选择适配器实现（fake/live/file），由 providers 唯一消费；
openai_* 经 .env.local 注入（已 gitignore，勿入库）。
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """运行配置（环境变量 MEGURI_* 注入；字段即文档，见各行注释）。"""

    database_url: str = "postgresql+psycopg://meguri:meguri@localhost:5432/meguri"
    adapter_mode: Literal["fake", "live"] = "fake"
    # 圣地数据源独立开关（与 LLM 的 adapter_mode 解耦，见 #4）：
    # live 直连 anitabi；file 用本地离线数据包（data/seichi/，真实 anitabi
    # 切片——anitabi 网络不可达时的 demo 模式）；fake 供测试。
    seichi_mode: Literal["fake", "live", "file"] = "live"
    seichi_data_dir: str = "data/seichi"
    # 开发 debug 模式：true 时 anitabi 完全不触网，lite/points 返回固定
    # 罐头数据（K-ON! 京都切片）；生产/发布前置 false。其余逻辑不变。
    debug_mode: bool = False
    # 交通数据源（#6）：live = 本地 OTP 服务。
    # 默认 fake（不依赖重服务）；dev.sh / compose 显式开 live。
    # OTP 不可达时 Navigator 自动降级为估算段（degraded 标记），不报错。
    transit_mode: Literal["fake", "live"] = "fake"
    otp_base_url: str = "http://localhost:8081/otp"
    # RAG 语料库（#8）：live = pgvector（同库 corpus_chunks 表）+ embedding
    # （有 openai_api_key 用 OpenAI 兼容真向量，无 key 用确定性哈希向量——
    # 检索链路真实、向量 fake）。
    corpus_mode: Literal["fake", "live"] = "fake"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    # 对话/工具调用/讲解生成的 chat 模型（.env.local 注入，勿入库）
    openai_model: str = "kimi-for-coding"
    # 向量维度：models 的 Vector 列与 EmbeddingProvider 共用此值。
    # 改维度必须重建语料（DROP TABLE corpus_chunks + 重新灌库）。
    embedding_dim: int = 64

    model_config = {
        "env_prefix": "MEGURI_",
        # .env.local（已 gitignore）注入 openai key 等；不存在时静默跳过——
        # 测试从 backend/  cwd 运行找不到它，且 testsupport 已固定全 fake
        "env_file": ".env.local",
    }


@lru_cache
def get_settings() -> Settings:
    """进程级单例 Settings；测试改环境变量后需 get_settings.cache_clear() 重建。"""
    return Settings()
