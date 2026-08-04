"""数据库连接与建表：SQLAlchemy engine（单例）+ 会话依赖 + 启动期建表。

会话/行程/语料数据同库（meguri，含 pgvector 扩展）；尚无 Alembic，
schema 变更靠 create_all（新表有效，旧表加列需重建）。
"""

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import get_settings

_engine = None


class Base(DeclarativeBase):
    """所有持久化模型的声明式基类（models.py 注册表）。"""


def _get_engine():
    """进程级单例 engine（惰性创建；connect_timeout 防止启动时长时间挂起）。"""
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, connect_args={"connect_timeout": 2})
    return _engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency：每请求一个数据库会话。"""
    with Session(_get_engine()) as session:
        yield session


def init_db() -> None:
    """启动时建表（尚无 Alembic，见后续 ticket）；RAG 语料需要 pgvector 扩展。"""
    from app import models  # noqa: F401  (导入以注册表)

    engine = _get_engine()
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(engine)


def check_db_health() -> str:
    """探测 DB 可用性：SELECT 1 成功返回 "up"，任何异常返回 "down"（供健康检查）。"""
    try:
        with _get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return "up"
    except Exception:
        return "down"
