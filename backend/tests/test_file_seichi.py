"""离线数据包模式（seichi_mode=file）：FileSeichiRepository 单测 + HTTP 缝行为测试。

数据包 = 真实 anitabi 数据的离线切片（data/seichi/115908.json，9 个宇治×京吹圣地）。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeLLMGateway
from app.adapters.file_seichi import FileSeichiRepository
from app.adapters.providers import get_llm_gateway, get_seichi_repository
from app.main import app

DATA_DIR = "data/seichi"  # 仓库内置真实数据包（相对仓库根解析，cwd 无关）


# --- FileSeichiRepository 单测 ---


def test_find_work_关键词匹配():
    repo = FileSeichiRepository(DATA_DIR)

    for keyword in ("上低音号", "吹响吧！上低音号", "響け！ユーフォニアム"):
        ref = repo.find_work(keyword)
        assert ref is not None
        assert ref.subject_id == 115908
        assert ref.name == "吹响吧！上低音号"
        assert ref.city == "宇治市"


def test_find_work_未收录作品返回None():
    assert FileSeichiRepository(DATA_DIR).find_work("完全不存在的作品xyz") is None


def test_find_work_多季作品具体季优先且忽略空白():
    """全量索引子串匹配：具体季查询词比总名长，不会被一期截胡；查询词与
    索引名两侧的空白都忽略（"轻音少女第二季" 也能命中 "轻音少女 第二季"）。"""
    repo = FileSeichiRepository(DATA_DIR)

    assert repo.find_work("轻音少女 剧场版").subject_id == 12426
    assert repo.find_work("轻音少女第二季").subject_id == 3774
    assert repo.find_work("轻音少女").subject_id == 1424  # 不带季词落回一期（最短名）


def test_find_work_空或纯空白输入返回None(tmp_path):
    """空查询包含于任何字符串，会误命中索引首条——必须直接 None。"""
    works = tmp_path / "anime-2000plus.json"
    works.write_text(json.dumps([{"id": 1, "name": "X", "name_cn": "Y"}]))
    repo = FileSeichiRepository(data_dir=str(tmp_path), works_file=str(works))

    assert repo.find_work("") is None
    assert repo.find_work("   ") is None


def test_works索引只读一次盘(tmp_path, monkeypatch):
    """9MB 全量索引进程内缓存：连续 find_work 不重复读盘（mtime 未变）。"""
    works = tmp_path / "anime-2000plus.json"
    works.write_text(json.dumps([
        {"id": 1, "name": "A", "name_cn": "甲"},
        {"id": 2, "name": "B", "name_cn": "乙"},
    ]))
    read_calls = []
    from pathlib import Path

    real_read_text = Path.read_text

    def counting_read_text(self, *args, **kwargs):
        if self.name == "anime-2000plus.json":
            read_calls.append(str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    repo = FileSeichiRepository(data_dir=str(tmp_path), works_file=str(works))

    repo.find_work("甲")
    repo.find_work("乙")

    assert len(read_calls) == 1  # 第二次命中缓存


def test_works索引_mtime变化才重读(tmp_path):
    works = tmp_path / "anime-2000plus.json"
    works.write_text(json.dumps([{"id": 1, "name": "A", "name_cn": "甲"}]))
    repo = FileSeichiRepository(data_dir=str(tmp_path), works_file=str(works))
    assert repo.find_work("甲") is not None

    import os, time

    time.sleep(0.01)
    os.utime(works)  # mtime 变化 → 重读
    works.write_text(json.dumps([{"id": 1, "name": "A", "name_cn": "改"}]))
    os.utime(works)
    assert repo.find_work("改") is not None


def test_缺数据目录优雅降级为空(tmp_path):
    # works_file 也指向缺失路径：缺省会回退到仓库内置全量索引（能解析作品名）
    repo = FileSeichiRepository(str(tmp_path), works_file=str(tmp_path / "none.json"))

    assert repo.find_work("上低音号") is None
    assert repo.search_seichi("上低音号", "宇治") == []


def test_find_work_多个包含匹配取最短名(tmp_path):
    """查"你的名字"应命中《你的名字。》而非名字更长的《死神剧场版 …呼唤着你的名字》。"""
    works = tmp_path / "works.json"
    works.write_text(json.dumps([
        {"id": 2875, "name": "死神剧场版 消逝于黑暗中 呼唤着你的名字", "name_cn": ""},
        {"id": 32281, "name": "君の名は。", "name_cn": "你的名字。"},
    ]))
    repo = FileSeichiRepository(data_dir=str(tmp_path), works_file=str(works))

    ref = repo.find_work("你的名字")

    assert ref is not None
    assert ref.subject_id == 32281


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
    results = FileSeichiRepository(DATA_DIR).search_seichi("上低音号", "东京")
    assert results == []


# --- 多作品命中：解析全保留、检索合并、区域外告知 ---


def test_resolve_works_多作品全部命中():
    """"轻音少女" 命中第一季/第二季/剧场版三条，按名字短→长排序。"""
    refs = FileSeichiRepository(DATA_DIR).resolve_works("轻音少女")

    assert [r.subject_id for r in refs] == [1424, 3774, 12426]
    assert refs[0].name == "轻音少女"  # 数据包权威名
    assert refs[0].city == "京都市"


def test_search_seichi_多作品合并与区域外告知():
    """区域"京都"：一期+二期点合并返回（各自带 work 标记）；
    剧场版（欧洲）整部作品被滤掉 → out_of_area 告知而非丢弃。"""
    repo = FileSeichiRepository(DATA_DIR)

    results = repo.search_seichi("轻音少女", "京都")

    assert {s.work for s in results} == {"轻音少女", "轻音少女 二期"}
    assert len(results) == 48 + 76
    assert repo.out_of_area == [
        {"work": "轻音少女 剧场版", "city": "欧洲", "count": 51}
    ]


def test_search_seichi_单作品无歧义时行为不变():
    repo = FileSeichiRepository(DATA_DIR)

    results = repo.search_seichi("上低音号", "宇治")

    assert len(results) == 8
    assert repo.out_of_area == []  # 部分点被滤掉不算"整部作品区域外"


# --- HTTP 缝行为测试：file repo 端到端 ---


def test_file数据包驱动三天行程():
    """file repo 走 HTTP 缝：'宇治三天京吹' → 真实 8 圣地的 3 天行程。"""
    # LLM 用启发式测试替身（生产已无 fake 装配，测试经 override 显式注入）
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway()
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
    # 刷新后快照仍在
    fresh = TestClient(app)
    assert fresh.get(f"/api/conversations/{cid}/itinerary").json()["itinerary"] == itinerary


def test_多作品检索_区域外摘要随响应返回():
    """HTTP 缝：'轻音少女' 命中三部曲 → 候选合并（带 work 标记），
    剧场版（欧洲）进 out_of_area 随响应透出。"""
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway(scripted=[
        json.dumps({"type": "tool_call", "name": "search_seichi",
                    "args": {"ani_name": "轻音少女", "area": "京都"}}),
        json.dumps({"type": "final", "content": "第一季和第二季共 124 处，剧场版在欧洲"}),
    ])
    app.dependency_overrides[get_seichi_repository] = lambda: FileSeichiRepository(DATA_DIR)
    client = TestClient(app)
    cid = client.post("/api/conversations").json()["conversation_id"]

    response = client.post(
        f"/api/conversations/{cid}/messages", json={"text": "轻音少女京都巡礼"}
    )

    assert response.status_code == 200
    body = response.json()
    assert {s["work"] for s in body["seichi"]} == {"轻音少女", "轻音少女 二期"}
    assert len(body["seichi"]) == 48 + 76
    assert body["out_of_area"] == [
        {"work": "轻音少女 剧场版", "city": "欧洲", "count": 51}
    ]


def test_规划路径_区域外摘要不进notice():
    """out_of_area 由模型在回复正文转告（观察文本带告知指令），
    不进 notice——用户拍板：这类提示在聊天流里看，不弹 toast。"""
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway(scripted=[
        json.dumps({"type": "tool_call", "name": "plan_itinerary",
                    "args": {"ani_name": "轻音少女", "area": "京都", "days": 2}}),
        json.dumps({"type": "final", "content": "两天行程；剧场版在欧洲，以后可单独规划"}),
    ])
    app.dependency_overrides[get_seichi_repository] = lambda: FileSeichiRepository(DATA_DIR)
    client = TestClient(app)
    cid = client.post("/api/conversations").json()["conversation_id"]

    response = client.post(
        f"/api/conversations/{cid}/messages", json={"text": "轻音少女京都两天"}
    )

    body = response.json()
    assert body["itinerary"] is not None
    assert body["notice"] is None  # 区域外信息不弹 toast
    assert body["out_of_area"] == [
        {"work": "轻音少女 剧场版", "city": "欧洲", "count": 51}
    ]


@pytest.mark.parametrize("mode_env", ["file"])
def test_file模式经provider装配(mode_env, monkeypatch):
    from app.adapters import providers

    monkeypatch.setenv("MEGURI_SEICHI_MODE", "file")
    providers.get_settings.cache_clear()
    try:
        assert isinstance(providers.get_seichi_repository(), FileSeichiRepository)
    finally:
        providers.get_settings.cache_clear()
