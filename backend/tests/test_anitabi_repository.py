"""AnitabiSeichiRepository 的解析逻辑测试。

用 2026-08 从真实 api.anitabi.cn / api.bgm.tv 抓取（并裁剪）的响应体，
经 httpx.MockTransport 回放，验证 live adapter 对真实数据结构的映射。
（对外的行为测试在 test_seichi_search.py，fake 打在 SeichiRepository 端口上。）
"""

import json

import httpx

from app.adapters.anitabi import AnitabiSeichiRepository

# --- 真实响应（裁剪）：POST api.bgm.tv/v0/search/subjects，keyword=吹响吧！上低音号
BGM_SEARCH_RESPONSE = {
    "data": [
        {"id": 115908, "name": "響け！ユーフォニアム", "name_cn": "吹响吧！上低音号"},
        {"id": 152091, "name": "響け！ユーフォニアム2", "name_cn": "吹响吧！上低音号 第二季"},
    ]
}

# --- 真实响应（裁剪）：GET api.anitabi.cn/bangumi/115908/lite
ANITABI_LITE_RESPONSE = {
    "id": 115908,
    "cn": "吹响吧！上低音号",
    "title": "響け！ユーフォニアム",
    "city": "宇治市",
    "pointsLength": 577,
}

# --- 真实响应（裁剪）：GET api.anitabi.cn/bangumi/115908/points/detail?haveImage=true
ANITABI_POINTS_RESPONSE = [
    {
        "id": "7gs3o1mm",
        "cn": "宇治桥",
        "name": "宇治橋",
        "image": "https://image.anitabi.cn/points/115908/7gs3o1mm.jpg?plan=h160",
        "ep": 2,
        "s": 809,
        "geo": [34.8929, 135.8065],
        "origin": "Anitabi@卜卜口",
        "originURL": "https://anitabi.cn/",
    },
    {
        "id": "qys7k4",
        "name": "大吉山展望台 蓝调",
        "image": "https://image.anitabi.cn/user/0/bangumi/115908/points/qys7k4-1715518655607.jpg?plan=h160",
        "ep": 8,
        "s": 1131,
        "geo": [34.8926, 135.8125],
        "origin": "Google Maps",
        "originURL": "https://www.google.com/maps/d/viewer?mid=13mgdlajJV0HxpqKf6ri2NnEHFBc",
    },
]


def make_repo() -> AnitabiSeichiRepository:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.bgm.tv":
            return httpx.Response(200, json=BGM_SEARCH_RESPONSE)
        if request.url.path.endswith("/lite"):
            if "115908" in request.url.path:
                return httpx.Response(200, json=ANITABI_LITE_RESPONSE)
            return httpx.Response(404)  # 第二季在 anitabi 无数据（假设）
        if request.url.path.endswith("/points/detail"):
            return httpx.Response(200, json=ANITABI_POINTS_RESPONSE)
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return AnitabiSeichiRepository(client=client)


def test_真实响应结构映射为候选圣地():
    repo = make_repo()

    results = repo.search_seichi("吹响吧！上低音号", "宇治")

    assert len(results) == 2
    first = results[0]
    assert first.name == "宇治桥"  # 优先中文译名
    assert (first.lat, first.lng) == (34.8929, 135.8065)
    assert first.image.startswith("https://image.anitabi.cn/points/115908/")
    assert first.ep == 2
    assert first.ep_seconds == 809
    assert first.origin == "Anitabi@卜卜口"
    assert first.work == "吹响吧！上低音号"
    assert first.area == "宇治市"
    # 无中文译名的地标回退原名
    assert results[1].name == "大吉山展望台 蓝调"


def test_地区不匹配时返回空():
    repo = make_repo()

    assert repo.search_seichi("吹响吧！上低音号", "东京") == []


def test_网络故障降级为空结果():
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = httpx.Client(transport=httpx.MockTransport(failing_handler))
    repo = AnitabiSeichiRepository(client=client)

    assert repo.search_seichi("吹响吧！上低音号", "宇治") == []


def test_检索参数直通_bgm搜索用作品名():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.bgm.tv":
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"data": []})
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    repo = AnitabiSeichiRepository(client=client)
    repo.search_seichi("吹响吧！上低音号", "宇治")

    assert seen == [{"keyword": "吹响吧！上低音号", "filter": {"type": [2]}}]
