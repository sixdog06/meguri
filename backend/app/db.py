from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import get_settings

_engine = None


class Base(DeclarativeBase):
    pass


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, connect_args={"connect_timeout": 2})
    return _engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency：每请求一个数据库会话。"""
    with Session(_get_engine()) as session:
        yield session


def init_db() -> None:
    """启动时建表（尚无 Alembic，见后续 ticket）。"""
    from app import models  # noqa: F401  (导入以注册表)

    Base.metadata.create_all(_get_engine())


def check_db_health() -> str:
    try:
        with _get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return "up"
    except Exception:
        return "down"
