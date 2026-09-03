"""最终数据层设计（用户拍板）：本地只做 ID↔名字映射，anitabi 实时查询的
两种显式结果——故障（503）与"该作品没有圣地巡礼数据"（notice）。

离线测试：anitabi 用 stub 客户端；Bangumi 不经网络（本地映射主路径）。
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.anitabi import (
    AnitabiClient,
    AnitabiSeichiRepository,
    AnitabiWorkSeichi,
    InvalidAnitabiResponse,
    NoSeichiData,
    SeichiSourceUnavailable,
)
from app.adapters.fakes import FakeLLMGateway
from app.adapters.file_seichi import FileSeichiRepository
from app.adapters.ports import Seichi
from app.adapters.providers import get_llm_gateway, get_seichi_repository
from app.main import app

WORK = "吹响吧！上低音号"


@pytest.fixture()
def mapping(tmp_path):
    """本地 ID↔名字映射夹具：1990 全量索引（中日文名）。"""
    works = tmp_path / "anime-1990plus.json"
    works.write_text(json.dumps([
        {"id": 115908, "name": "響け！ユーフォニアム", "name_cn": "吹响吧！上低音号",
         "air_date": "2015-04-07"},
        {"id": 216134, "name": "ゆるキャン△", "name_cn": "摇曳露营△", "air_date": "2018-01-04"},
    ]))
    return FileSeichiRepository(data_dir=str(tmp_path), works_file=str(works))


# --- 映射表查询 ---


def test_映射表按中日文名匹配(mapping):
    assert mapping.resolve_works("上低音号")[0].subject_id == 115908
    assert mapping.resolve_works("ユーフォニアム")[0].subject_id == 115908
    # 俗名不再命中：别名索引已删，只有官方名的子串能解析（LLM 负责归一化）
    assert mapping.resolve_works("京吹") == []
    assert mapping.resolve_works("ゆるキャン")[0].subject_id == 216134
    assert mapping.resolve_works("不存在的作品") == []


# --- AnitabiSeichiRepository 的两种显式结果 ---


class StubAnitabi:
    def __init__(self, *, error=None, result=None):
        self._error = error
        self._result = result

    def fetch_seichi(self, subject_id, work_fallback=""):
        if self._error:
            raise self._error
        return self._result


def test_anitabi故障_抛SeichiSourceUnavailable(mapping):
    repo = AnitabiSeichiRepository(mapping, client=StubAnitabi(error=httpx.ConnectError("boom")))

    with pytest.raises(SeichiSourceUnavailable, match="暂时不可用"):
        repo.search_seichi("上低音号", "宇治")


def test_curl_cffi故障_同样抛Unavailable(mapping):
    """线上默认客户端是 curl_cffi（Cloudflare 封 httpx TLS 指纹），其异常也要映射 503。"""
    from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError

    repo = AnitabiSeichiRepository(
        mapping, client=StubAnitabi(error=CurlConnectionError("boom"))
    )
    with pytest.raises(SeichiSourceUnavailable):
        repo.search_seichi("上低音号", "宇治")


# --- debug 模式（MEGURI_DEBUG_MODE）：anitabi 不触网，罐头数据 ---


def test_debug客户端_不触网返回罐头数据(mapping, monkeypatch):
    import app.adapters.anitabi as anitabi_mod

    def no_network(*args, **kwargs):
        raise AssertionError("debug 模式不得触网")

    monkeypatch.setattr(anitabi_mod.curl_requests, "get", no_network)
    repo = AnitabiSeichiRepository(mapping, client=AnitabiClient(debug=True))

    results = repo.search_seichi("上低音号", "京都")

    assert len(results) > 40  # 罐头 K-ON! 切片全量返回（按 max_results 截断前）
    assert all(s.area == "京都市" for s in results)
    assert results[0].work == "上低音号"  # lite cn 置空 → 回退为查询串（与 live 同语义）
    assert "ブランデンブルク門" not in {s.name for s in results}  # 柏林污染点被距离过滤


def test_debug客户端_地区不匹配仍过滤(mapping):
    repo = AnitabiSeichiRepository(mapping, client=AnitabiClient(debug=True))

    assert repo.search_seichi("上低音号", "东京") == []  # 罐头城市是京都市


def test_间隙页故障_同样抛Unavailable(mapping):
    repo = AnitabiSeichiRepository(
        mapping, client=StubAnitabi(error=InvalidAnitabiResponse("非 JSON"))
    )
    with pytest.raises(SeichiSourceUnavailable):
        repo.search_seichi("上低音号", "宇治")


def test_anitabi空结果_抛NoSeichiData(mapping):
    repo = AnitabiSeichiRepository(mapping, client=StubAnitabi(result=None))  # 404

    with pytest.raises(NoSeichiData, match="没有圣地巡礼数据"):
        repo.search_seichi("上低音号", "宇治")


# --- anitabi 故障时的离线兜底（显式降级，绝不静默冒充实时数据） ---


@pytest.fixture()
def mapping_with_pack(mapping, tmp_path):
    """映射夹具 + 上低音号离线数据包（data/seichi/<id>.json 形态，供兜底路径）。"""
    pack = {
        "subject_id": 115908,
        "work": "吹响吧！上低音号",
        "city": "宇治市",
        "points": [
            {"id": "p1", "name": "宇治桥", "lat": 34.8929, "lng": 135.8065, "area": "宇治市"}
        ],
    }
    (tmp_path / "115908.json").write_text(json.dumps(pack, ensure_ascii=False))
    return mapping


def test_anitabi故障_本地有数据时显式兜底(mapping_with_pack):
    repo = AnitabiSeichiRepository(
        mapping_with_pack, client=StubAnitabi(error=httpx.ConnectError("boom"))
    )

    results = repo.search_seichi("上低音号", "宇治")

    assert [s.name for s in results] == ["宇治桥"]  # 离线包数据照常返回
    assert repo.fallback_notice is not None and "离线数据包" in repo.fallback_notice


def test_anitabi故障_本地无数据仍503(mapping):
    repo = AnitabiSeichiRepository(mapping, client=StubAnitabi(error=httpx.ConnectError("boom")))

    with pytest.raises(SeichiSourceUnavailable):
        repo.search_seichi("上低音号", "宇治")
    assert repo.fallback_notice is None  # 无兜底痕迹


def test_工具层透传兜底notice(mapping_with_pack):
    from app.agents.tools import SearchSeichiTool

    repo = AnitabiSeichiRepository(
        mapping_with_pack, client=StubAnitabi(error=httpx.ConnectError("boom"))
    )
    tool = SearchSeichiTool(repo)

    tool.run({"ani_name": "上低音号", "area": "宇治"})

    assert tool.notice is not None and "离线数据包" in tool.notice


def test_anitabi零地标_也算无数据(mapping):
    empty = AnitabiWorkSeichi(work_name=WORK, city="宇治市", seichi=[])
    repo = AnitabiSeichiRepository(mapping, client=StubAnitabi(result=empty))
    with pytest.raises(NoSeichiData):
        repo.search_seichi("上低音号", "宇治")


def test_未收录作品_普通空结果(mapping):
    repo = AnitabiSeichiRepository(
        mapping, client=StubAnitabi(error=AssertionError("不该触网"))
    )
    assert repo.search_seichi("不存在的作品", "宇治") == []


# --- 多作品命中：逐作品拉取、合并、区域外告知、部分失败 ---


@pytest.fixture()
def mapping_multi(tmp_path):
    """多作品映射夹具：轻音三部曲（第一季/第二季/剧场版）。"""
    works = tmp_path / "anime-1990plus.json"
    works.write_text(json.dumps([
        {"id": 1424, "name": "けいおん！", "name_cn": "轻音少女", "air_date": "2009-04-02"},
        {"id": 3774, "name": "けいおん！！", "name_cn": "轻音少女 第二季", "air_date": "2010-04-06"},
        {"id": 12426, "name": "映画けいおん！", "name_cn": "轻音少女 剧场版", "air_date": "2011-12-03"},
    ]))
    return FileSeichiRepository(data_dir=str(tmp_path), works_file=str(works))


class StubAnitabiMulti:
    """按 subjectID 分派的 stub：值是 AnitabiWorkSeichi/None 或异常实例。"""

    def __init__(self, by_subject):
        self._by = by_subject

    def fetch_seichi(self, subject_id, work_fallback=""):
        value = self._by[subject_id]
        if isinstance(value, Exception):
            raise value
        return value


def _work_seichi(name, city, point_names):
    return AnitabiWorkSeichi(
        work_name=name,
        city=city,
        seichi=[
            Seichi(id=f"{name}-{i}", name=n, work=name, area=city, lat=35.0, lng=135.0)
            for i, n in enumerate(point_names)
        ],
    )


def test_多作品命中_合并返回且区域外告知(mapping_multi):
    repo = AnitabiSeichiRepository(
        mapping_multi,
        client=StubAnitabiMulti({
            1424: _work_seichi("轻音少女", "京都市", ["A", "B"]),
            3774: _work_seichi("轻音少女 第二季", "京都府", ["C"]),
            12426: _work_seichi("轻音少女 剧场版", "欧洲", ["D", "E"]),
        }),
    )

    results = repo.search_seichi("轻音少女", "京都")

    assert [s.name for s in results] == ["A", "B", "C"]
    assert {s.work for s in results} == {"轻音少女", "轻音少女 第二季"}
    assert repo.out_of_area == [
        {"work": "轻音少女 剧场版", "city": "欧洲", "count": 2}
    ]


def test_多作品_部分失败有结果时降级提示而非503(mapping_multi):
    repo = AnitabiSeichiRepository(
        mapping_multi,
        client=StubAnitabiMulti({
            1424: _work_seichi("轻音少女", "京都市", ["A"]),
            3774: httpx.ConnectError("boom"),
            12426: httpx.ConnectError("boom"),
        }),
    )

    results = repo.search_seichi("轻音少女", "京都")

    assert [s.name for s in results] == ["A"]
    assert repo.fallback_notice is not None and "结果可能不全" in repo.fallback_notice


def test_多作品_全部失败才503(mapping_multi):
    repo = AnitabiSeichiRepository(
        mapping_multi,
        client=StubAnitabiMulti({
            1424: httpx.ConnectError("boom"),
            3774: httpx.ConnectError("boom"),
            12426: httpx.ConnectError("boom"),
        }),
    )
    with pytest.raises(SeichiSourceUnavailable):
        repo.search_seichi("轻音少女", "京都")


def test_多作品_部分无数据不抛错(mapping_multi):
    """一部无巡礼数据、另一部正常 → 返回正常那部，不抛 NoSeichiData。"""
    repo = AnitabiSeichiRepository(
        mapping_multi,
        client=StubAnitabiMulti({
            1424: None,
            3774: _work_seichi("轻音少女 第二季", "京都府", ["C"]),
            12426: None,
        }),
    )

    results = repo.search_seichi("轻音少女", "京都")

    assert [s.name for s in results] == ["C"]


def test_多作品_全部无数据才抛NoSeichiData(mapping_multi):
    repo = AnitabiSeichiRepository(
        mapping_multi,
        client=StubAnitabiMulti({1424: None, 3774: None, 12426: None}),
    )
    with pytest.raises(NoSeichiData, match="轻音少女"):
        repo.search_seichi("轻音少女", "京都")


# --- HTTP 缝：两种情形的前端可见区分 ---

SEARCH_SCRIPT = [
    json.dumps({"type": "tool_call", "name": "search_seichi", "args": {"ani_name": WORK, "area": "宇治"}}),
    json.dumps({"type": "final", "content": "这部作品在 anitabi 没有圣地巡礼数据。"}),
]


def make_client(repo) -> TestClient:
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway(scripted=list(SEARCH_SCRIPT))
    app.dependency_overrides[get_seichi_repository] = lambda: repo
    return TestClient(app)


def post(client: TestClient, text: str) -> tuple[int, dict]:
    cid = client.post("/api/conversations").json()["conversation_id"]
    response = client.post(f"/api/conversations/{cid}/messages", json={"text": text})
    return response.status_code, response.json()


def test_anitabi故障_API返回503且明确告知(mapping):
    repo = AnitabiSeichiRepository(mapping, client=StubAnitabi(error=httpx.ConnectError("boom")))
    client = make_client(repo)

    status, body = post(client, "宇治有哪些京吹圣地")

    assert status == 503
    assert "圣地数据服务暂时不可用" in body["detail"]  # 显式故障，不是 500


def test_anitabi无数据_200带结构化notice且回复如实转述(mapping):
    repo = AnitabiSeichiRepository(mapping, client=StubAnitabi(result=None))
    client = make_client(repo)

    status, body = post(client, "宇治有哪些京吹圣地")

    assert status == 200  # 不是错误
    assert body["seichi"] == []  # 也不是空列表静默
    assert "没有圣地巡礼数据" in body["notice"]  # 结构化标志
    assert "没有圣地巡礼数据" in body["reply"]  # 模型如实转述
