"""测试支持共享模块（#10 review）：环境装配 + schema 重建 + fake 客户端工厂。

backend/tests 与 eval/ 两处 conftest 共用，装配知识只此一份。
本模块被 import 时即固定测试环境（必须在任何 app 导入之前 import 它）。
"""

import json
import os
from pathlib import Path

# --- 环境装配（import 即生效，app 导入链读取 settings 前必须完成） ---
# 测试用独立的 meguri_test 库：与 dev 守护进程共用的 meguri 库隔离——
# 此前 reset_schema 会把用户 dev 数据一起 drop 掉。
os.environ["MEGURI_DATABASE_URL"] = "postgresql+psycopg://meguri:meguri@localhost:5433/meguri_test"
# 生产已无 fake 模式；测试替身经 HTTP 缝 dependency override 注入（见 make_client）。
# 兜底默认：seichi 钉在 file（纯离线数据包），保证未 override 的测试也绝不触网。
os.environ["MEGURI_SEICHI_MODE"] = "file"
# 同理剔除 shell 里可能导出的真实 key：未 override 的 LLM/embedding 路径
# 必须缺 key 报错或走哈希向量，不能误打真实 API。
for _key in (
    "MEGURI_OPENAI_API_KEY",
    "MEGURI_EMBEDDING_API_KEY",
    "MEGURI_EMBEDDING_BASE_URL",
):
    os.environ.pop(_key, None)
# .env.local 的 env_file 按 CWD 解析：从仓库根跑 pytest 会把开发者本地配置
# （真实 key、embedding 维度等）带进测试，破坏隔离。测试环境永不读 env_file。
from app.config import Settings  # noqa: E402

Settings.model_config["env_file"] = None

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _ensure_test_database() -> None:
    """meguri_test 不存在则创建（CREATE DATABASE 不能在事务里，autocommit）。"""
    import psycopg

    with psycopg.connect(
        "postgresql://meguri:meguri@localhost:5433/postgres", autocommit=True
    ) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = 'meguri_test'"
        ).fetchone()
        if not exists:
            conn.execute("CREATE DATABASE meguri_test")


def reset_schema() -> None:
    """重建 schema（行为测试/评测会话级隔离）。Postgres 用真实的（5433）。"""
    from sqlalchemy import text

    from app import db, models  # noqa: F401  (导入 models 以注册表)

    _ensure_test_database()
    engine = db._get_engine()
    db.Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))  # RAG 语料（#8）
        conn.commit()
    db.Base.metadata.create_all(engine)


def make_client(
    *,
    repo_seichi: list,
    llm_script: list[dict],
    transit_routes: list[dict] | None = None,
    chunks: list | None = None,
):
    """按夹具组装 HTTP 缝客户端（全 fake）+ trace 收集器。"""
    from fastapi.testclient import TestClient

    from app.adapters.fakes import (
        FakeLLMGateway,
        FakeSeichiRepository,
        FakeTransitClient,
    )
    from app.adapters.ports import CorpusChunk, Seichi
    from app.adapters.providers import (
        get_corpus_store,
        get_llm_gateway,
        get_seichi_repository,
        get_transit_client,
    )
    from app.agents.tracing import InMemoryTracer
    from app.api.conversations import get_tracer
    from app.main import app
    from app.rag.store import InMemoryCorpusStore

    def seichi_of(data):
        return data if isinstance(data, Seichi) else Seichi(**data)

    def chunk_of(data):
        return data if isinstance(data, CorpusChunk) else CorpusChunk(**data)

    scripted = [s if isinstance(s, str) else json.dumps(s, ensure_ascii=False) for s in llm_script]
    tracer = InMemoryTracer()
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway(scripted=scripted)
    app.dependency_overrides[get_seichi_repository] = lambda: FakeSeichiRepository(
        seichi=[seichi_of(s) for s in repo_seichi]
    )
    app.dependency_overrides[get_transit_client] = lambda: FakeTransitClient(
        scripted=list(transit_routes or [])
    )
    app.dependency_overrides[get_corpus_store] = lambda: InMemoryCorpusStore(
        chunks=[chunk_of(c) for c in chunks or []]
    )
    app.dependency_overrides[get_tracer] = lambda: tracer
    return TestClient(app), tracer
