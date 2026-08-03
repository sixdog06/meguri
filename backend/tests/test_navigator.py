"""Ticket #6：Navigator 交通与时间校验。

- 行为测试经 HTTP 缝驱动，TransitClient / OpeningHoursSource 用 fake 注入；
- OTPTransitClient 的 GraphQL 响应解析用真实形状的响应体经 MockTransport 回放
  （OTP 集成不进 pytest，graph 构建走 otp/ pipeline 脚本）。
"""

import json
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeLLMGateway, FakeOpeningHours, FakeSeichiRepository, FakeTransitClient
from app.adapters.otp import NoRouteError, OTPTransitClient
from app.adapters.ports import Seichi
from app.adapters.providers import (
    get_llm_gateway,
    get_opening_hours_source,
    get_seichi_repository,
    get_transit_client,
)
from app.agents.opening_hours import is_open
from app.main import app

WORK = "吹响吧！上低音号"
AREA = "宇治"


def _s(id: str, name: str, lat: float, lng: float) -> Seichi:
    return Seichi(
        id=id, name=name, work=WORK, area="宇治市", lat=lat, lng=lng,
        ep=1, ep_seconds=100, origin="fake",
    )


A1 = _s("a1", "宇治桥", 34.8929, 135.8065)
A3 = _s("a3", "久美子椅", 34.8896, 135.8075)
A2 = _s("a2", "宇治神社", 34.8905, 135.8099)
B1 = _s("b1", "京阪六地藏", 34.9321, 135.7935)
B2 = _s("b2", "六地藏站旁铁塔", 34.9315, 135.7941)
C1 = _s("c1", "山城综合运动公园", 34.8714, 135.8054)
FIXTURE = [A1, A2, A3, B1, B2, C1]

PLAN_SCRIPT = [
    json.dumps(
        {"type": "tool_call", "name": "plan_itinerary",
         "args": {"work": WORK, "area": AREA, "days": 3}}
    ),
    json.dumps({"type": "final", "content": "三天行程已生成"}),
]

# 5 段 legs（day1: 1 天内+1 跨天；day2: 2 天内+1 跨天；day3: 无）的真实查询结果
REAL_ROUTES = [
    {"mode": "walk", "duration_minutes": 2, "fare_yen": None, "estimate": False},
    {"mode": "transit", "duration_minutes": 18, "fare_yen": None, "estimate": False},
    {"mode": "walk", "duration_minutes": 10, "fare_yen": None, "estimate": False},
    {"mode": "walk", "duration_minutes": 10, "fare_yen": None, "estimate": False},
    {"mode": "transit", "duration_minutes": 12, "fare_yen": None, "estimate": False},
]


def make_client(
    transit: FakeTransitClient | None = None,
    hours: FakeOpeningHours | None = None,
) -> TestClient:
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway(scripted=list(PLAN_SCRIPT))
    app.dependency_overrides[get_seichi_repository] = lambda: FakeSeichiRepository(seichi=FIXTURE)
    app.dependency_overrides[get_transit_client] = lambda: transit or FakeTransitClient()
    app.dependency_overrides[get_opening_hours_source] = lambda: hours or FakeOpeningHours()
    return TestClient(app)


def plan(client: TestClient) -> dict:
    cid = client.post("/api/conversations").json()["conversation_id"]
    response = client.post(f"/api/conversations/{cid}/messages", json={"text": "宇治三天京吹"})
    assert response.status_code == 200
    return response.json()["itinerary"]


def test_真实查询替换估算交通段():
    transit = FakeTransitClient(scripted=list(REAL_ROUTES))
    client = make_client(transit=transit)

    itinerary = plan(client)

    legs = [leg for d in itinerary["days"] for leg in d["legs"]]
    assert len(legs) == 5
    assert len(transit.calls) == 5  # 每段都经 TransitClient 端口查询
    for leg, real in zip(legs, REAL_ROUTES):
        assert leg["mode"] == real["mode"]
        assert leg["duration_minutes"] == real["duration_minutes"]
        assert leg["estimate"] is False  # 真实数据，不再是估算
        assert leg["degraded"] is False
        assert leg["fare_yen"] is None  # 日本 GTFS 常缺票价，拿不到保持 None
    # 跨天段同样被替换
    connectors = [leg for leg in legs if leg["cross_day"]]
    assert [leg["mode"] for leg in connectors] == ["transit", "transit"]


def test_OTP不可达时保留估算并显式降级():
    class DownTransit(FakeTransitClient):
        def route(self, origin, destination, *, depart_at=None):
            raise httpx.ConnectError("connection refused")

    client = make_client(transit=DownTransit())

    itinerary = plan(client)

    legs = [leg for d in itinerary["days"] for leg in d["legs"]]
    assert len(legs) == 5
    for leg in legs:
        assert leg["estimate"] is True  # 保留原估算
        assert leg["degraded"] is True  # 显式降级提示
        assert leg["note"]
    assert itinerary["day_count"] == 3  # 行程仍可用


def test_开放时间校验_闭馆被标记():
    hours = FakeOpeningHours({
        (34.8929, 135.8065): "24/7",  # 宇治桥
        (34.8905, 135.8099): "Mo-Su 09:00-09:30",  # 宇治神社：计划到达时已过开放时间
    })
    transit = FakeTransitClient(scripted=list(REAL_ROUTES))
    client = make_client(transit=transit, hours=hours)

    itinerary = plan(client)

    day2 = next(d for d in itinerary["days"] if len(d["seichi"]) == 3)
    checks = {c["seichi_id"]: c for c in day2["checks"]}
    # 09:00 出发，每站停留 45 分钟，leg 耗时 10 分钟
    assert checks["a1"]["arrive_time"] == "09:00"
    assert checks["a1"]["open"] is True  # 24/7
    assert checks["a3"]["arrive_time"] == "09:55"
    assert checks["a3"]["open"] is None  # 无开放时间数据 → 不标记
    assert checks["a2"]["arrive_time"] == "10:50"
    assert checks["a2"]["open"] is False  # 到达时已闭馆
    assert checks["a2"]["note"]


def test_无交通数据源时估算段保留且不标降级():
    """FakeTransitClient 返回 estimate=True（无真实数据）= 静默保留估算，不算故障。"""
    client = make_client()

    itinerary = plan(client)

    legs = [leg for d in itinerary["days"] for leg in d["legs"]]
    assert all(leg["estimate"] is True and leg["degraded"] is False for leg in legs)


# --- opening_hours 子集解析 ---


@pytest.mark.parametrize(
    "oh, at, weekday, expected",
    [
        ("24/7", "03:00", 0, True),
        ("Mo-Fr 09:00-17:00", "10:00", 0, True),  # 周一
        ("Mo-Fr 09:00-17:00", "10:00", 5, False),  # 周六
        ("Mo-Fr 09:00-17:00", "18:00", 0, False),
        ("Mo-Su 09:00-17:00", "12:00", 6, True),
        ("09:00-17:00", "12:00", 2, True),  # 无日部分 = 每天
        ("Tu-Su 09:00-17:00", "12:00", 0, False),  # 周一休馆
        ("Mo-Fr 09:00-12:00,13:00-17:00", "12:30", 1, False),  # 午休
        ("Mo-Fr 09:00-12:00,13:00-17:00", "13:30", 1, True),
        ("not a real format !!!", "12:00", 0, None),  # 无法解析 → 未知
        ("Mo-Fr", "12:00", 0, None),  # 无时间部分 → 无法判断
    ],
)
def test_opening_hours子集解析(oh, at, weekday, expected):
    hh, mm = at.split(":")
    from datetime import time

    assert is_open(oh, time(int(hh), int(mm)), weekday) is expected


# --- OTPTransitClient 响应解析（真实形状响应回放） ---

OTP_PLAN_RESPONSE = {
    "data": {
        "plan": {
            "itineraries": [
                {
                    "duration": 1500,
                    "legs": [
                        {"mode": "WALK", "distance": 320.5},
                        {"mode": "BUS", "distance": 4300.0},
                        {"mode": "WALK", "distance": 210.0},
                    ],
                }
            ]
        }
    }
}


def make_otp_client(handler) -> OTPTransitClient:
    return OTPTransitClient(
        base_url="http://otp.test/otp",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_OTP响应解析_含公交段为transit():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/otp/routers/default/index/graphql"
        body = json.loads(request.content)
        assert "34.8929,135.8065" in body["query"] or "fromPlace" in str(body.get("variables"))
        return httpx.Response(200, json=OTP_PLAN_RESPONSE)

    client = make_otp_client(handler)
    result = client.route((34.8929, 135.8065), (34.9321, 135.7935))

    assert result["mode"] == "transit"  # 含 BUS 段
    assert result["duration_minutes"] == 25
    assert result["estimate"] is False
    assert result["fare_yen"] is None


def test_OTP响应解析_有fare时映射fare_yen():
    """GTFS 含票价时：各 leg 的 fareProducts 价格（JPY）求和为 fare_yen。"""
    response = {
        "data": {
            "plan": {
                "itineraries": [
                    {
                        "duration": 1500,
                        "legs": [
                            {"mode": "WALK", "distance": 320.5, "fareProducts": []},
                            {
                                "mode": "BUS",
                                "distance": 4300.0,
                                "fareProducts": [
                                    {"product": {"price": {"amount": 230.0, "currency": {"code": "JPY"}}}}
                                ],
                            },
                            {
                                "mode": "RAIL",
                                "distance": 1200.0,
                                "fareProducts": [
                                    {"product": {"price": {"amount": 160.0, "currency": {"code": "JPY"}}}}
                                ],
                            },
                        ],
                    }
                ]
            }
        }
    }
    client = make_otp_client(lambda request: httpx.Response(200, json=response))

    result = client.route((34.8929, 135.8065), (34.9321, 135.7935))

    assert result["mode"] == "transit"
    assert result["fare_yen"] == 390


def test_OTP请求带depart_at时按其格式化_缺省用东京时区():
    """naive/缺省时间不能跟容器 UTC 偏移：缺省时应按 Asia/Tokyo 当前时间发。"""
    from zoneinfo import ZoneInfo

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content)["variables"])
        return httpx.Response(200, json={"data": {"plan": {"itineraries": []}}})

    client = make_otp_client(handler)
    try:
        client.route((34.8929, 135.8065), (34.9321, 135.7935))
    except NoRouteError:
        pass

    tokyo_now = datetime.now(ZoneInfo("Asia/Tokyo"))
    assert captured["date"] == tokyo_now.strftime("%Y-%m-%d")
    assert abs(
        int(captured["time"].split(":")[0]) * 60 + int(captured["time"].split(":")[1])
        - (tokyo_now.hour * 60 + tokyo_now.minute)
    ) <= 1


def test_OTP响应解析_纯步行为walk():
    response = {
        "data": {"plan": {"itineraries": [{"duration": 600, "legs": [{"mode": "WALK", "distance": 800.0}]}]}}
    }
    client = make_otp_client(lambda request: httpx.Response(200, json=response))

    result = client.route((34.8929, 135.8065), (34.8896, 135.8075))

    assert result["mode"] == "walk"
    assert result["duration_minutes"] == 10


def test_OTP无路线时抛NoRoute():
    client = make_otp_client(
        lambda request: httpx.Response(200, json={"data": {"plan": {"itineraries": []}}})
    )

    with pytest.raises(NoRouteError):
        client.route((34.8929, 135.8065), (35.0, 135.0))


def test_OTP长距离纯步行_标记公交未覆盖降级():
    """请求了 TRANSIT 但只有步行结果且距离超阈值 = GTFS 未覆盖 → 明确降级提示。"""
    response = {
        "data": {"plan": {"itineraries": [{"duration": 3960, "legs": [{"mode": "WALK", "distance": 4800.0}]}]}}
    }
    client = make_otp_client(lambda request: httpx.Response(200, json=response))

    result = client.route((34.8929, 135.8065), (34.9321, 135.7935))  # 约 4.4km

    assert result["mode"] == "walk"
    assert result["estimate"] is False  # 真实路网步行耗时，仍是有用数据
    assert result["degraded"] is True
    assert "覆盖" in result["note"]


def test_OTP短距离纯步行不算降级():
    response = {
        "data": {"plan": {"itineraries": [{"duration": 600, "legs": [{"mode": "WALK", "distance": 800.0}]}]}}
    }
    client = make_otp_client(lambda request: httpx.Response(200, json=response))

    result = client.route((34.8929, 135.8065), (34.8896, 135.8075))

    assert result.get("degraded") is not True
    assert result.get("note") is None


def test_Navigator传播降级标记与提示():
    scripted = [
        {"mode": "walk", "duration_minutes": 2, "fare_yen": None, "estimate": False},
        {"mode": "walk", "duration_minutes": 66, "fare_yen": None, "estimate": False,
         "degraded": True, "note": "公共交通数据未覆盖，按步行路网计算"},
        {"mode": "walk", "duration_minutes": 10, "fare_yen": None, "estimate": False},
        {"mode": "walk", "duration_minutes": 10, "fare_yen": None, "estimate": False},
        {"mode": "walk", "duration_minutes": 30, "fare_yen": None, "estimate": False,
         "degraded": True, "note": "公共交通数据未覆盖，按步行路网计算"},
    ]
    client = make_client(transit=FakeTransitClient(scripted=scripted))

    itinerary = plan(client)

    legs = [leg for d in itinerary["days"] for leg in d["legs"]]
    degraded = [leg for leg in legs if leg["degraded"]]
    assert len(degraded) == 2
    for leg in degraded:
        assert leg["estimate"] is False  # 真实步行耗时，但明确标记降级
        assert "覆盖" in leg["note"]
