# 必须在导入 app 之前设置环境变量：settings 在 app 导入链中即被读取。
import os

os.environ["MEGURI_DATABASE_URL"] = "postgresql+psycopg://meguri:meguri@localhost:5433/meguri"
# 测试不触网：圣地数据源固定 fake（各测试再经 dependency_overrides 注入固定数据集）
os.environ["MEGURI_SEICHI_MODE"] = "fake"
os.environ["MEGURI_TRANSIT_MODE"] = "fake"
os.environ["MEGURI_HOURS_MODE"] = "fake"

import pytest

from app.config import get_settings

get_settings.cache_clear()

from app.main import app


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture(scope="session", autouse=True)
def reset_db_schema():
    """每个测试会话重建一次 schema，保证隔离。Postgres 用真实的。"""
    from app import db, models  # noqa: F401  (导入 models 以注册表)

    engine = db._get_engine()
    db.Base.metadata.drop_all(engine)
    db.Base.metadata.create_all(engine)
    yield
