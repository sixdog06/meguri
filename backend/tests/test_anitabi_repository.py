"""AnitabiClient 的解析/故障语义测试。

用 2026-08 从真实 api.anitabi.cn 抓取（并裁剪）的响应体，经 httpx.MockTransport
回放验证；线上"作品名→ID"不在此层（本地 ID 库承担，见 file_seichi 测试）。
"""

import httpx
import pytest

from app.adapters.anitabi import AnitabiClient

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


def make_client() -> AnitabiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/lite"):
            return httpx.Response(200, json=ANITABI_LITE_RESPONSE)
        if request.url.path.endswith("/points/detail"):
            return httpx.Response(200, json=ANITABI_POINTS_RESPONSE)
        return httpx.Response(500)

    return AnitabiClient(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_距主城市过远的污染点被过滤():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/lite"):
            return httpx.Response(200, json={
                "id": 1424, "cn": "轻音少女", "city": "京都市",
                "geo": [35.0116, 135.7681],
            })
        return httpx.Response(200, json=[
            {"id": "near", "name": "修学院駅", "geo": [35.0505, 135.7904]},
            {"id": "daytrip", "name": "由良川橋", "geo": [35.5108, 135.288]},  # ~65km 日归，保留
            {"id": "berlin", "name": "ブランデンブルク門", "geo": [52.5162, 13.3781]},  # 污染点
        ])

    client = AnitabiClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    result = client.fetch_seichi(1424)

    assert result is not None
    assert {s.name for s in result.seichi} == {"修学院駅", "由良川橋"}


def test_真实响应结构映射为候选圣地():
    result = make_client().fetch_seichi(115908)

    assert result is not None
    assert result.work_name == "吹响吧！上低音号"
    assert result.city == "宇治市"
    seichi = result.seichi
    assert len(seichi) == 2
    first = seichi[0]
    assert first.name == "宇治桥"  # 优先中文译名
    assert (first.lat, first.lng) == (34.8929, 135.8065)
    assert first.image.startswith("https://image.anitabi.cn/points/115908/")
    assert first.ep == 2
    assert first.ep_seconds == 809
    assert first.origin == "Anitabi@卜卜口"
    assert first.work == "吹响吧！上低音号"
    assert first.area == "宇治市"
    # 无中文译名的地标回退原名
    assert seichi[1].name == "大吉山展望台 蓝调"


def test_lite_404返回None():
    client = AnitabiClient(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(404))
        )
    )

    assert client.fetch_lite(999999) is None
    assert client.fetch_seichi(999999) is None


def test_网络故障原样上抛_由仓库层映射为SeichiSourceUnavailable():
    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = AnitabiClient(
        client=httpx.Client(transport=httpx.MockTransport(failing))
    )

    with pytest.raises(httpx.ConnectError):
        client.fetch_lite(115908)
    with pytest.raises(httpx.ConnectError):
        client.fetch_points(115908)


def test_非JSON间隙页_抛InvalidAnitabiResponse():
    """Cloudflare 200+HTML 间隙页：解析层错误按故障上抛（编程错误不掩）。"""
    from app.adapters.anitabi import InvalidAnitabiResponse

    client = AnitabiClient(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, text="<html>Attention Required!</html>")
            )
        )
    )

    with pytest.raises(InvalidAnitabiResponse):
        client.fetch_lite(115908)
    with pytest.raises(InvalidAnitabiResponse):
        client.fetch_points(115908)
