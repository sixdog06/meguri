"""OTP/Overpass 进程级缓存（编辑后 revalidate 的提速手段，见各模块注释）。

行为断言：相同查询第二次不打 HTTP；故障（Overpass）不缓存、下次重试。
"""

import testsupport  # noqa: F401

import httpx

from app.adapters.otp import OTPTransitClient
from app.adapters.overpass import OverpassOpeningHours

_PLAN_RESPONSE = {
    "data": {
        "plan": {
            "itineraries": [
                {"duration": 600, "legs": [{"mode": "WALK", "distance": 800, "fareProducts": []}]}
            ]
        }
    }
}


def test_otp相同路线第二次走缓存():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=_PLAN_RESPONSE)

    client = OTPTransitClient(base_url="http://cache-test-otp", client=httpx.Client(transport=httpx.MockTransport(handler)))
    origin, dest = (35.0, 135.8), (35.01, 135.81)

    first = client.route(origin, dest)
    second = client.route(origin, dest)

    assert len(calls) == 1  # 第二次命中缓存
    assert first == second
    second["mode"] = "被调用方篡改"
    assert client.route(origin, dest)["mode"] == "walk"  # 缓存不被调用方污染


def test_overpass相同坐标第二次走缓存_无数据也缓存():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json={"elements": [{"tags": {"opening_hours": "Mo-Su 09:00-17:00"}}]})
        return httpx.Response(200, json={"elements": []})

    client = OverpassOpeningHours(url="http://cache-test-overpass", client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.opening_hours(35.0, 135.8) == "Mo-Su 09:00-17:00"
    assert client.opening_hours(35.0, 135.8) == "Mo-Su 09:00-17:00"
    assert len(calls) == 1  # 第二次命中缓存

    assert client.opening_hours(35.1, 135.9) is None  # 无数据
    assert client.opening_hours(35.1, 135.9) is None
    assert len(calls) == 2  # "无数据"是确定答案，缓存不重查


def test_overpass源故障置标记_TTL内不再打网():
    import app.adapters.overpass as overpass_mod

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("boom")

    overpass_mod._down_until = 0.0
    client = OverpassOpeningHours(url="http://down-test-overpass", client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.opening_hours(35.2, 135.1) is None  # 第一次：真打网，失败置标记
    assert len(calls) == 1
    assert client.opening_hours(35.3, 135.2) is None  # TTL 内：不再打网
    assert client.opening_hours(35.4, 135.3) is None
    assert len(calls) == 1

    overpass_mod._down_until = 0.0  # 模拟 TTL 过期
    assert client.opening_hours(35.3, 135.2) is None  # 重试打网
    assert len(calls) == 2
    overpass_mod._down_until = 0.0  # 不污染其它测试


def test_prefetch_单请求填满缓存():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"elements": [
            {"type": "node", "lat": 35.0001, "lon": 135.8001, "tags": {"opening_hours": "Mo-Fr 09:00-18:00"}},
            {"type": "way", "center": {"lat": 35.0101, "lon": 135.8101}, "tags": {"opening_hours": "24/7"}},
        ]})

    client = OverpassOpeningHours(url="http://prefetch-test-overpass", client=httpx.Client(transport=httpx.MockTransport(handler)))
    coords = [(35.0, 135.8), (35.01, 135.81), (35.5, 135.3)]

    client.prefetch(coords)

    assert len(calls) == 1  # 全部站点一次请求
    assert client.opening_hours(35.0, 135.8) == "Mo-Fr 09:00-18:00"  # node 命中
    assert client.opening_hours(35.01, 135.81) == "24/7"  # way（center）命中
    assert client.opening_hours(35.5, 135.3) is None  # 200m 内无元素 → None（也缓存）
    assert len(calls) == 1  # 后续单站查询全部走缓存
