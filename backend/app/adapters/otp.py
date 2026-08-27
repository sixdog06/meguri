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

import time
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


# 进程级路线缓存：编辑后的 revalidate 会重查全行程的段，但绝大多数段与
# 规划时相同——缓存命中后编辑从分钟级降到秒级（单进程 dev 部署语义，与
# EventBus 一致；多实例部署需换外部缓存）。键含端点，测试互不污染。
_ROUTE_CACHE: dict[tuple[str, tuple[float, float], tuple[float, float]], dict[str, Any]] = {}

# 进程级熔断：OTP 容器在但 graph 未加载完（或半死）时，每次查询都要吃满
# 超时——n 站点矩阵 n²−n 次逐对请求可卡 30+ 分钟，且每个用户请求重踩一遍。
# 连续失败 _BREAKER_THRESHOLD 次后打开熔断，TTL 内 route()/duration_matrix()
# 直接返回 None（Navigator 降级为估算段/保持原顺序）；TTL 过后半开试探，
# 成功复位计数、失败重新打开。失败 = 请求异常/超时/非 200/GraphQL 错误
# （NoRouteError 是覆盖范围的确定答案，不算故障）。
# 模块级、进程内共享（与 _ROUTE_CACHE 同语义；多实例部署需换外部状态）。
_BREAKER_THRESHOLD = 3
_BREAKER_TTL_SECONDS = 60.0
_breaker_failures = 0
_breaker_open_until = 0.0


def _breaker_open() -> bool:
    """熔断是否处于打开的 TTL 内（TTL 过后放行半开试探）。"""
    return time.monotonic() < _breaker_open_until


def _record_breaker_failure() -> None:
    """记一次失败：连续失败达阈值则打开熔断 TTL。"""
    global _breaker_failures, _breaker_open_until
    _breaker_failures += 1
    if _breaker_failures >= _BREAKER_THRESHOLD:
        _breaker_open_until = time.monotonic() + _BREAKER_TTL_SECONDS


def _record_breaker_success() -> None:
    """请求成功（含半开试探）：复位失败计数与熔断。"""
    global _breaker_failures, _breaker_open_until
    _breaker_failures = 0
    _breaker_open_until = 0.0


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
        # trust_env=False：本地服务绝不能走系统代理（macOS 下 getproxies 会
        # 读系统偏好里的代理，把 localhost 请求也劫持过去）
        self._client = client or httpx.Client(timeout=timeout, trust_env=False)

    def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        *,
        depart_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """点间路线查询：返回真实路网/换乘结果（见端口契约）；空路线/异常上抛。

        长距离纯步行（GTFS 未覆盖）结果附带 degraded/note 降级提示。

        结果按（端点, 起, 讫）进程内缓存（短时内墙钟差异不影响规划语义）。
        熔断打开的 TTL 内直接返回 None（不打网，调用方降级为估算段）。
        """
        key = (self._endpoint, origin, destination)
        if key in _ROUTE_CACHE:
            return dict(_ROUTE_CACHE[key])
        if _breaker_open():
            return None  # 熔断 TTL 内不重踩故障（见模块级注释）
        at = depart_at or datetime.now(_TOKYO)
        try:
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
        except (httpx.HTTPError, ValueError):
            _record_breaker_failure()
            raise
        _record_breaker_success()
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
        _ROUTE_CACHE[key] = result
        return dict(result)

    def duration_matrix(
        self,
        points: list[tuple[float, float]],
        *,
        depart_at: datetime | None = None,
    ) -> list[list[int | None]] | None:
        """耗时矩阵（分钟，有向）：逐对 route 查询（进程内缓存——随后 Navigator
        逐段解析命中缓存，不重复请求）。

        失败/estimate 的条目为 None（调用方回退距离估算）；全部失败返回 None
        （调用方保持原顺序）。供 Navigator 的天内顺序优化用。
        熔断打开时整个矩阵立即返回 None，不逐对空转。
        """
        if _breaker_open():
            return None  # 熔断 TTL 内不重踩故障（见模块级注释）
        n = len(points)
        matrix: list[list[int | None]] = [[None] * n for _ in range(n)]
        got_real = False
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                try:
                    result = self.route(points[i], points[j], depart_at=depart_at)
                except Exception:  # 单对失败不拖累全矩阵：留 None 由调用方回退
                    continue
                if result is None:
                    return None  # 循环中途熔断打开：不再逐对空转
                if result.get("estimate"):
                    continue
                matrix[i][j] = int(result["duration_minutes"])
                got_real = True
        return matrix if got_real else None
