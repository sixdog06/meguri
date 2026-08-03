from sqlalchemy import create_engine, text

from app.config import get_settings

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, connect_args={"connect_timeout": 2})
    return _engine


def check_db_health() -> str:
    try:
        with _get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return "up"
    except Exception:
        return "down"
