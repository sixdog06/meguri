# 环境装配与 schema 重建集中在 backend/testsupport（import 即固定测试环境，
# 必须先于 app 导入）。
import testsupport  # noqa: F401

import pytest

from app.config import get_settings

get_settings.cache_clear()

from app.main import app


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clear_adapter_caches():
    """OTP/Overpass 的进程级缓存（生产跨请求共享）在测试间必须隔离。"""
    from app.adapters import otp, overpass

    otp._ROUTE_CACHE.clear()
    overpass._HOURS_CACHE.clear()
    yield


@pytest.fixture(scope="session", autouse=True)
def reset_db_schema():
    """每个测试会话重建一次 schema，保证隔离。Postgres 用真实的。"""
    testsupport.reset_schema()
    yield
