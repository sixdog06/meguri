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


def test_overpass相同坐标第二次走缓存_故障不缓存():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json={"elements": [{"tags": {"opening_hours": "Mo-Su 09:00-17:00"}}]})
        if len(calls) == 2:
            return httpx.Response(500)  # 故障：不缓存，第三次重试
        return httpx.Response(200, json={"elements": []})

    client = OverpassOpeningHours(url="http://cache-test-overpass", client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.opening_hours(35.0, 135.8) == "Mo-Su 09:00-17:00"
    assert client.opening_hours(35.0, 135.8) == "Mo-Su 09:00-17:00"
    assert len(calls) == 1  # 第二次命中缓存

    assert client.opening_hours(35.1, 135.9) is None  # 故障返回 None 但不缓存
    assert client.opening_hours(35.1, 135.9) is None  # 重试（第三次调用，无数据）
    assert len(calls) == 3
    assert client.opening_hours(35.1, 135.9) is None
    assert len(calls) == 3  # "无数据"是确定答案，缓存不重查
