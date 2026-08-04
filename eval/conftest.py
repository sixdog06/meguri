"""评测 harness 的 pytest 环境（与 backend/tests 行为测试分离，不进 CI 门禁）。

运行方式（仓库根目录）：
  .venv/bin/python -m pytest eval/ -v -s
环境装配与 schema 重建集中在 backend/testsupport（与行为测试共享）。
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import testsupport  # noqa: E402,F401  (import 即固定测试环境)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    from app.main import app

    yield
    app.dependency_overrides.clear()


@pytest.fixture(scope="session", autouse=True)
def reset_db_schema():
    """评测用真实 Postgres（5433），会话级重建 schema。"""
    testsupport.reset_schema()
    yield
