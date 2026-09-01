"""应用配置：全部运行时开关集中于此（MEGURI_ 环境变量前缀 + .env.local）。

生产只保留真实适配器：LLM/交通/语料库只有一种实现（live），无模式开关；
seichi_mode 是唯一保留的开关（live 直连 anitabi / file 纯离线数据包）。
openai_* 经 .env.local 注入（已 gitignore，勿入库）。
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """运行配置（环境变量 MEGURI_* 注入；字段即文档，见各行注释）。"""

    database_url: str = "postgresql+psycopg://meguri:meguri@localhost:5432/meguri"
    # 圣地数据源开关（#4）：live 直连 anitabi；file 用本地离线数据包
    # （data/seichi/，真实 anitabi 切片——anitabi 网络不可达时的 demo 模式）。
    seichi_mode: Literal["live", "file"] = "live"
    seichi_data_dir: str = "data/seichi"
    # 开发 debug 模式：true 时 anitabi 完全不触网，lite/points 返回固定
    # 罐头数据（K-ON! 京都切片）；生产/发布前置 false。其余逻辑不变。
    debug_mode: bool = False
    # 交通（#6）：唯一实现是本地 OTP 服务（GraphQL）。
    # OTP 不可达时 Navigator 自动降级为估算段（degraded 标记），不报错。
    otp_base_url: str = "http://localhost:8081/otp"
    # RAG 语料库（#8）：唯一实现是 pgvector（同库 corpus_chunks 表）+ embedding
    # （有 embedding/openai key 用 OpenAI 兼容真向量，无 key 用确定性哈希向量——
    # 检索链路真实、向量 fake）。
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    # embedding 独立端点（可与 chat LLM 不同服务）：缺省回退 openai_base_url /
    # openai_api_key。本地 Ollama（bge-m3）：MEGURI_EMBEDDING_BASE_URL=
    # http://localhost:11434/v1、MEGURI_EMBEDDING_API_KEY=ollama（任意非空即可）
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    # 检索相似度阈值下限（余弦）：0.6 按哈希向量分布标定；真 embedding
    # （bge-m3 等）分布不同，按实测调低（见 rag/embedding.py 的标定注释）
    corpus_min_score: float = 0.6
    # 对话/工具调用/讲解生成的 chat 模型（.env.local 注入，勿入库）
    openai_model: str = "kimi-for-coding"
    # 向量维度：models 的 Vector 列与 EmbeddingProvider 共用此值。
    # 改维度必须重建语料（DROP TABLE corpus_chunks + 重新灌库）。
    embedding_dim: int = 64

    model_config = {
        "env_prefix": "MEGURI_",
        # .env.local（已 gitignore）注入 openai key 等；不存在时静默跳过——
        # 测试从 backend/ cwd 运行找不到它，且 testsupport 已禁用 env_file
        "env_file": ".env.local",
    }


@lru_cache
def get_settings() -> Settings:
    """进程级单例 Settings；测试改环境变量后需 get_settings.cache_clear() 重建。"""
    return Settings()
