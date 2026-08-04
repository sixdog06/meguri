"""离线数据包模式（seichi_mode=file）：FileSeichiRepository 单测 + HTTP 缝行为测试。

数据包 = 真实 anitabi 数据的离线切片（data/seichi/115908.json，9 个宇治×京吹圣地）。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.adapters.file_seichi import FileSeichiRepository
from app.adapters.providers import get_seichi_repository
from app.main import app

DATA_DIR = "data/seichi"  # 仓库内置真实数据包（相对仓库根解析，cwd 无关）


# --- FileSeichiRepository 单测 ---


def test_find_work_关键词匹配():
    repo = FileSeichiRepository(DATA_DIR)

    for keyword in ("京吹", "吹响吧！上低音号", "響け！ユーフォニアム"):
        ref = repo.find_work(keyword)
        assert ref is not None
        assert ref.subject_id == 115908
        assert ref.name == "吹响吧！上低音号"
        assert ref.city == "宇治市"


def test_find_work_未收录作品返回None():
    assert FileSeichiRepository(DATA_DIR).find_work("轻音少女") is None


def test_缺数据目录优雅降级为空(tmp_path):
    repo = FileSeichiRepository(str(tmp_path))

    assert repo.find_work("京吹") is None
    assert repo.search_seichi("京吹", "宇治") == []


def test_search_seichi_返回真实数据切片():
    repo = FileSeichiRepository(DATA_DIR)

    results = repo.search_seichi("吹响吧！上低音号", "宇治")

    assert len(results) == 8  # 9 个点减去京都市的京都音乐厅
    first = next(s for s in results if s.id == "7gs3o1mm")
    assert first.name == "宇治桥"
    assert (first.lat, first.lng) == (34.8929, 135.8065)
    assert first.image.startswith("https://image.anitabi.cn/points/115908/")
    assert first.ep == 2
    assert first.ep_seconds == 809
    assert first.origin == "Anitabi@卜卜口"
    assert first.work == "吹响吧！上低音号"
    assert first.area == "宇治市"


def test_search_seichi_区域过滤():
    results = FileSeichiRepository(DATA_DIR).search_seichi("京吹", "东京")
    assert results == []


# --- HTTP 缝行为测试：file repo 端到端 ---


def test_file数据包驱动三天行程():
    """seichi_mode=file 的 repo 走 HTTP 缝：'宇治三天京吹' → 真实 8 圣地的 3 天行程。"""
    app.dependency_overrides[get_seichi_repository] = lambda: FileSeichiRepository(DATA_DIR)
    client = TestClient(app)
    cid = client.post("/api/conversations").json()["conversation_id"]

    response = client.post(
        f"/api/conversations/{cid}/messages", json={"text": "宇治三天京吹"}
    )

    assert response.status_code == 200
    body = response.json()
    itinerary = body["itinerary"]
    assert itinerary is not None, "离线数据包下不得再说'没有找到候选圣地'"
    assert itinerary["day_count"] == 3
    stops = [s for d in itinerary["days"] for s in d["seichi"]]
    assert len(stops) == 8
    assert {s["name"] for s in stops} >= {"宇治桥", "宇治神社", "久美子椅"}
    # 预算结构在（真实票价缺失 → 未计价，不编）
    assert itinerary["budget"] is not None
    # 刷新后快照仍在
    fresh = TestClient(app)
    assert fresh.get(f"/api/conversations/{cid}/itinerary").json()["itinerary"] == itinerary


@pytest.mark.parametrize("mode_env", ["file"])
def test_file模式经provider装配(mode_env, monkeypatch):
    from app.adapters import providers

    monkeypatch.setenv("MEGURI_SEICHI_MODE", "file")
    providers.get_settings.cache_clear()
    try:
        assert isinstance(providers.get_seichi_repository(), FileSeichiRepository)
    finally:
        providers.get_settings.cache_clear()
