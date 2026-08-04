"""Ticket #7：预算服务（确定性汇总与超支告警）。

- 纯函数单测：summarize_budget 的加总、分项、None 票价、超支边界；
- HTTP 缝行为测试：带预算请求 → 响应含预算结构、超支告警、刷新后仍在。
全程不经过 LLM（预算服务是确定性模块，见 CONTEXT.md）。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeLLMGateway, FakeOpeningHours, FakeSeichiRepository, FakeTransitClient
from app.adapters.ports import Seichi
from app.adapters.providers import (
    get_llm_gateway,
    get_opening_hours_source,
    get_seichi_repository,
    get_transit_client,
)
from app.agents.budget import summarize_budget
from app.agents.planner import ItineraryDay, ItinerarySnapshot, TransitLeg
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


def _leg(a: Seichi, b: Seichi, fare: int | None, mode: str = "transit") -> TransitLeg:
    return TransitLeg(
        from_id=str(a.id), to_id=str(b.id), mode=mode,
        distance_km=1.0, duration_minutes=10, estimate=False, fare_yen=fare,
    )


def make_snapshot() -> ItinerarySnapshot:
    return ItinerarySnapshot(
        day_count=1,
        days=[
            ItineraryDay(
                day=1,
                seichi=[A1, A3, A2],
                legs=[_leg(A1, A3, 230), _leg(A3, A2, None)],
            )
        ],
    )


def test_步行段fare缺失按确定的0元计_不算未计价():
    """fare_yen=None 两种含义：GTFS 缺票价（非步行）vs 步行本来就免费。"""
    snapshot = ItinerarySnapshot(
        day_count=1,
        days=[
            ItineraryDay(
                day=1,
                seichi=[A1, A3, A2],
                legs=[_leg(A1, A3, None, mode="walk"), _leg(A3, A2, 230)],
            )
        ],
    )

    report = summarize_budget(snapshot)

    assert report.transit[0].amount_yen == 0  # 步行段 → 确定的 ¥0
    assert report.total_yen == 230
    assert report.unpriced_count == 0 + 3  # 只有门票未计价


def test_非步行段fare缺失才是未计价():
    """drive/transit 的 None = 票价数据缺失，不能当免费。"""
    snapshot = ItinerarySnapshot(
        day_count=1,
        days=[
            ItineraryDay(
                day=1,
                seichi=[A1, A3, A2],
                legs=[_leg(A1, A3, None, mode="drive"), _leg(A3, A2, None)],
            )
        ],
    )

    report = summarize_budget(snapshot)

    assert report.transit[0].amount_yen is None
    assert report.unpriced_count == 2 + 3


# --- 纯函数单测 ---


def test_加总与分项明细():
    report = summarize_budget(make_snapshot())

    assert report.total_yen == 230
    assert [(i.label, i.amount_yen) for i in report.transit] == [
        ("宇治桥→久美子椅", 230),
        ("久美子椅→宇治神社", None),
    ]
    # 门票：无数据源时每站一项、未计价（大部分圣地免费，但不编 0）
    assert [(i.label, i.amount_yen) for i in report.admission] == [
        ("宇治桥", None),
        ("久美子椅", None),
        ("宇治神社", None),
    ]


def test_None票价为未计价项_不计入合计():
    report = summarize_budget(make_snapshot())

    assert report.unpriced_count == 1 + 3  # 1 段交通 + 3 站门票
    assert report.total_yen == 230  # None 不当 0 计入，只是没有别的可加


def test_门票作为可选输入计入合计():
    report = summarize_budget(make_snapshot(), admission_yen={"a2": 600})

    assert report.total_yen == 230 + 600
    assert report.admission[2].amount_yen == 600
    assert report.unpriced_count == 1 + 2


@pytest.mark.parametrize(
    "limit, total_expected, over_expected",
    [
        (None, 230, False),  # 无上限 → 不超支
        (500, 230, False),  # 低于上限
        (230, 230, False),  # 恰好等于上限 → 不超支
        (229, 230, True),  # 超 1 日元也算超支
    ],
)
def test_超支边界(limit, total_expected, over_expected):
    report = summarize_budget(make_snapshot(), limit_yen=limit)

    assert report.total_yen == total_expected
    assert report.over_budget is over_expected
    if over_expected:
        assert report.alert is not None
        assert "1" in report.alert  # 超出金额
    else:
        assert report.alert is None


def test_全部未计价时合计为零且不误报超支():
    snapshot = make_snapshot()
    snapshot.days[0].legs[0].fare_yen = None

    report = summarize_budget(snapshot, limit_yen=100)

    assert report.total_yen == 0
    assert report.over_budget is False  # 未计价不参与超支判断
    assert report.unpriced_count == 2 + 3


# --- HTTP 缝行为测试 ---

FARED_ROUTES = [
    {"mode": "walk", "duration_minutes": 2, "fare_yen": None, "estimate": False},
    {"mode": "transit", "duration_minutes": 18, "fare_yen": 230, "estimate": False},
    {"mode": "walk", "duration_minutes": 10, "fare_yen": None, "estimate": False},
    {"mode": "walk", "duration_minutes": 10, "fare_yen": None, "estimate": False},
    {"mode": "transit", "duration_minutes": 12, "fare_yen": 460, "estimate": False},
]

FIXTURE = [
    A1, A3, A2,
    _s("b1", "京阪六地藏", 34.9321, 135.7935),
    _s("b2", "六地藏站旁铁塔", 34.9315, 135.7941),
    _s("c1", "山城综合运动公园", 34.8714, 135.8054),
]


def make_client(scripted_llm: list[str]) -> TestClient:
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway(scripted=scripted_llm)
    app.dependency_overrides[get_seichi_repository] = lambda: FakeSeichiRepository(seichi=FIXTURE)
    app.dependency_overrides[get_transit_client] = lambda: FakeTransitClient(scripted=list(FARED_ROUTES))
    app.dependency_overrides[get_opening_hours_source] = lambda: FakeOpeningHours()
    return TestClient(app)


def plan(client: TestClient, text: str = "宇治三天京吹") -> dict:
    cid = client.post("/api/conversations").json()["conversation_id"]
    response = client.post(f"/api/conversations/{cid}/messages", json={"text": text})
    assert response.status_code == 200
    return response.json(), cid


def plan_script(budget_yen: int | None) -> list[str]:
    args = {"work": WORK, "area": AREA, "days": 3}
    if budget_yen is not None:
        args["budget_yen"] = budget_yen
    return [
        json.dumps({"type": "tool_call", "name": "plan_itinerary", "args": args}),
        json.dumps({"type": "final", "content": "三天行程已生成"}),
    ]


def test_带预算请求_响应含预算结构且超支告警():
    client = make_client(plan_script(budget_yen=500))

    body, _ = plan(client)

    budget = body["itinerary"]["budget"]
    assert budget["limit_yen"] == 500
    assert budget["total_yen"] == 230 + 460  # 两段计价交通
    assert budget["over_budget"] is True
    assert "190" in budget["alert"]  # 超出 190 日元
    assert budget["unpriced_count"] == 0 + 6  # 步行段按 ¥0 计，只有 6 站门票未计价
    # 分项明细
    assert len(budget["transit"]) == 5
    assert len(budget["admission"]) == 6
    # 步行段 fare 缺失按 ¥0 计（确定免费），非步行的真实票价保留
    assert [i["amount_yen"] for i in budget["transit"]] == [0, 230, 0, 0, 460]


def test_预算快照持久化_刷新可见():
    client = make_client(plan_script(budget_yen=500))
    _, cid = plan(client)

    fresh = TestClient(app)
    budget = fresh.get(f"/api/conversations/{cid}/itinerary").json()["itinerary"]["budget"]

    assert budget["total_yen"] == 690
    assert budget["over_budget"] is True


def test_无预算上限时不超支无告警():
    client = make_client(plan_script(budget_yen=None))

    body, _ = plan(client)

    budget = body["itinerary"]["budget"]
    assert budget["limit_yen"] is None
    assert budget["total_yen"] == 690
    assert budget["over_budget"] is False
    assert budget["alert"] is None
    assert budget["unpriced_count"] == 0 + 6


def test_启发式fake_LLM识别预算数字():
    """dev 演示路径：'预算 N 日元' 进入 plan_itinerary 的 budget_yen。"""
    repo = FakeSeichiRepository(seichi=FIXTURE)
    app.dependency_overrides[get_llm_gateway] = lambda: FakeLLMGateway()  # 无脚本
    app.dependency_overrides[get_seichi_repository] = lambda: repo
    app.dependency_overrides[get_transit_client] = lambda: FakeTransitClient(scripted=list(FARED_ROUTES))
    app.dependency_overrides[get_opening_hours_source] = lambda: FakeOpeningHours()
    client = TestClient(app)

    body, _ = plan(client, "宇治三天京吹，预算500日元")

    budget = body["itinerary"]["budget"]
    assert budget["limit_yen"] == 500
    assert budget["over_budget"] is True
