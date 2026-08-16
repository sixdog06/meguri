"""OTP 进程级缓存（编辑后 revalidate 的提速手段，见 otp 模块注释）。

行为断言：相同查询第二次不打 HTTP；缓存不被调用方污染。
"""

import testsupport  # noqa: F401

import httpx

from app.adapters.otp import OTPTransitClient

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
