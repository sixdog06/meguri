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
    """本地 ID↔名字映射夹具：别名（京吹）+ 1990 索引（中日文名）。"""
    (tmp_path / "index.json").write_text(json.dumps({"京吹": 115908}))
    works = tmp_path / "anime-1990plus.json"
    works.write_text(json.dumps([
        {"id": 115908, "name": "響け！ユーフォニアム", "name_cn": "吹响吧！上低音号",
         "air_date": "2015-04-07"},
        {"id": 216134, "name": "ゆるキャン△", "name_cn": "摇曳露营△", "air_date": "2018-01-04"},
    ]))
    return FileSeichiRepository(data_dir=str(tmp_path), works_file=str(works))


# --- 映射表查询 ---


def test_映射表按中日文名匹配(mapping):
    assert mapping.find_work("上低音号").subject_id == 115908
    assert mapping.find_work("ユーフォニアム").subject_id == 115908
    assert mapping.find_work("京吹").subject_id == 115908  # 别名索引
    assert mapping.find_work("ゆるキャン").subject_id == 216134
    assert mapping.find_work("不存在的作品") is None


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
        repo.search_seichi("京吹", "宇治")


def test_间隙页故障_同样抛Unavailable(mapping):
    repo = AnitabiSeichiRepository(
        mapping, client=StubAnitabi(error=InvalidAnitabiResponse("非 JSON"))
    )
    with pytest.raises(SeichiSourceUnavailable):
        repo.search_seichi("京吹", "宇治")


def test_anitabi空结果_抛NoSeichiData(mapping):
    repo = AnitabiSeichiRepository(mapping, client=StubAnitabi(result=None))  # 404

    with pytest.raises(NoSeichiData, match="没有圣地巡礼数据"):
        repo.search_seichi("京吹", "宇治")


def test_anitabi零地标_也算无数据(mapping):
    empty = AnitabiWorkSeichi(work_name=WORK, city="宇治市", seichi=[])
    repo = AnitabiSeichiRepository(mapping, client=StubAnitabi(result=empty))
    with pytest.raises(NoSeichiData):
        repo.search_seichi("京吹", "宇治")


def test_未收录作品_普通空结果(mapping):
    repo = AnitabiSeichiRepository(
        mapping, client=StubAnitabi(error=AssertionError("不该触网"))
    )
    assert repo.search_seichi("不存在的作品", "宇治") == []


# --- HTTP 缝：两种情形的前端可见区分 ---

SEARCH_SCRIPT = [
    json.dumps({"type": "tool_call", "name": "search_seichi", "args": {"work": WORK, "area": "宇治"}}),
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
