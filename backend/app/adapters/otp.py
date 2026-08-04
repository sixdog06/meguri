"""TransitClient 的 live 实现：OpenTripPlanner 2.x（ADR-0003）。

经 OTP2 的 GraphQL API（{base}/routers/default/index/graphql）发 plan 查询
（WALK+TRANSIT），返回真实路网/换乘的耗时；fare 经 legs 的 fareProducts
（GTFS 票价数据）映射 JPY 求和为 fare_yen——日本 GTFS 常缺票价数据，拿不到
保持 None（不编）。

时间语义：OTP 按墙钟日期/时间查询，naive 缺省时间按 Asia/Tokyo 当前时间
（容器时区可能是 UTC，不能直接用 naive now）。

降级语义（验收：GTFS 未覆盖/OTP 异常要"明确降级提示而非报错或沉默"）：
- 查询返回空 itineraries（坐标在 graph 覆盖范围外等）→ 抛 NoRouteError
- 网络/服务异常 → httpx 异常上抛
由 Navigator 统一转成 degraded 估算段。
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.geo import haversine_km

_TOKYO = ZoneInfo("Asia/Tokyo")

_QUERY = """
query Plan($fromPlace: String!, $toPlace: String!, $date: String!, $time: String!) {
  plan(fromPlace: $fromPlace, toPlace: $toPlace, date: $date, time: $time,
       transportModes: [{mode: WALK}, {mode: TRANSIT}]) {
    itineraries {
      duration
      legs {
        mode
        distance
        fareProducts {
          product {
            ... on DefaultFareProduct { price { amount currency { code } } }
          }
        }
      }
    }
  }
}
"""

# 请求了 TRANSIT 但只有步行结果且直线距离超过此值 → 视为公共交通未覆盖（降级提示）
_NO_TRANSIT_KM = 2.0


class NoRouteError(Exception):
    """OTP 返回空路线：坐标不在 graph 覆盖范围（如 GTFS/OSM 未覆盖区域）。"""


def _fare_yen(itinerary: dict[str, Any]) -> int | None:
    """各 leg 的 fareProducts 价格（JPY）求和；无任何票价数据返回 None。"""
    total = 0.0
    found = False
    for leg in itinerary.get("legs", []):
        for use in leg.get("fareProducts") or []:
            price = (use.get("product") or {}).get("price") or {}
            currency = price.get("currency") or {}
            code = currency.get("code") if isinstance(currency, dict) else currency
            if code == "JPY" and price.get("amount") is not None:
                total += price["amount"]
                found = True
    return round(total) if found else None


class OTPTransitClient:
    """TransitClient 的 OTP 实现：构造可注入 httpx.Client 便于回放测试。"""

    def __init__(
        self,
        base_url: str = "http://localhost:8081/otp",
        *,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/routers/default/index/graphql"
        self._client = client or httpx.Client(timeout=timeout)

    def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        *,
        depart_at: datetime | None = None,
    ) -> dict[str, Any]:
        """点间路线查询：返回真实路网/换乘结果（见端口契约）；空路线/异常上抛。

        长距离纯步行（GTFS 未覆盖）结果附带 degraded/note 降级提示。
        """
        at = depart_at or datetime.now(_TOKYO)
        response = self._client.post(
            self._endpoint,
            json={
                "query": _QUERY,
                "variables": {
                    "fromPlace": f"{origin[0]},{origin[1]}",
                    "toPlace": f"{destination[0]},{destination[1]}",
                    "date": at.strftime("%Y-%m-%d"),
                    "time": at.strftime("%H:%M"),
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise ValueError(f"OTP GraphQL 错误: {payload['errors'][0].get('message')}")
        itineraries = (payload.get("data") or {}).get("plan", {}).get("itineraries") or []
        if not itineraries:
            raise NoRouteError("该区域不在交通图覆盖范围内")
        best = min(itineraries, key=lambda i: i["duration"])
        modes = {leg["mode"] for leg in best.get("legs", [])}
        result: dict[str, Any] = {
            "mode": "walk" if modes <= {"WALK"} else "transit",
            "duration_minutes": max(1, round(best["duration"] / 60)),
            "fare_yen": _fare_yen(best),
            "estimate": False,
        }
        # GTFS 未覆盖（graph 只含路网）时，长距离也只有步行结果——真实步行
        # 耗时保留，但给明确降级提示（验收：未覆盖要提示而非沉默）
        if result["mode"] == "walk" and haversine_km(
            origin[0], origin[1], destination[0], destination[1]
        ) > _NO_TRANSIT_KM:
            result["degraded"] = True
            result["note"] = "公共交通数据未覆盖，按步行路网计算"
        return result
