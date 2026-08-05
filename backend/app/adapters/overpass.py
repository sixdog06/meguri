"""OpeningHoursSource 的 live 实现：Overpass API 查 OSM opening_hours 标签。

按坐标就近取 200m 内带 opening_hours 的元素（圣地多为 OSM 已有 POI）。
查询失败/无数据一律返回 None（= 开放时间未知，不误标）。

全行程校验走 prefetch 批量预取（一次请求拿全部站点，避免逐站 47 连发
撞限速/故障）；单站 opening_hours 兜底。
"""

import time

import httpx

from app.geo import haversine_km

_RADIUS_M = 200

# 进程级缓存（与 otp._ROUTE_CACHE 同语义）：编辑后的 revalidate 重查全行程
# 的开放时间，Overpass 是远程限速 API，无缓存时每次编辑分钟级——缓存后秒级。
_HOURS_CACHE: dict[tuple[float, float], str | None] = {}

# 源故障标记：Overpass 不可达时（本机网络对其间歇性 ConnectError）单站查询
# 每次都要等连接失败（~1s/站 × 几十站 = 分钟级）。置标记后 TTL 内直接
# 返回 None（= 开放时间未知，不误标），TTL 过后再试。
_SOURCE_DOWN_TTL_SECONDS = 60.0
_down_until = 0.0


def _mark_source_down() -> None:
    """置源故障标记（模块级，进程内共享；TTL 见常量注释）。"""
    global _down_until
    _down_until = time.monotonic() + _SOURCE_DOWN_TTL_SECONDS


class OverpassOpeningHours:
    """OpeningHoursSource 的 Overpass 实现（构造可注入 httpx.Client 便于测试）。"""

    def __init__(
        self,
        url: str = "https://overpass-api.de/api/interpreter",
        *,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = url
        self._client = client or httpx.Client(timeout=timeout)

    def opening_hours(self, lat: float, lng: float) -> str | None:
        """查坐标附近的 OSM opening_hours 标签；失败/无数据返回 None（不误标）。

        结果按坐标进程内缓存（含 None——无数据也是确定答案，不重查）；
        源故障置 _down_until 标记，TTL 内不再打网。
        """
        key = (lat, lng)
        if key in _HOURS_CACHE:
            return _HOURS_CACHE[key]
        if time.monotonic() < _down_until:
            return None  # 源故障 TTL 内直接视为未知（见模块级注释）
        query = (
            f"[out:json];(node(around:{_RADIUS_M},{lat},{lng})[opening_hours];"
            f"way(around:{_RADIUS_M},{lat},{lng})[opening_hours];);out tags 5;"
        )
        try:
            response = self._client.post(self._url, data={"data": query})
            response.raise_for_status()
            elements = response.json().get("elements") or []
        except (httpx.HTTPError, ValueError):
            _mark_source_down()
            return None  # 故障不缓存结果：TTL 过后重试（与"无数据"区分）
        value = None
        for element in elements:
            value = (element.get("tags") or {}).get("opening_hours")
            if value:
                break
        _HOURS_CACHE[key] = value or None
        return value or None

    def prefetch(self, coords: list[tuple[float, float]]) -> None:
        """一次请求批量取全部站点的 opening_hours 填入缓存（校验前置）。

        逐站几十连发会撞 Overpass 限速/本机网络故障（每站 ~1s 等待连接
        失败 = 编辑卡分钟级）；合并成单请求后成功秒填、失败只等一次
        （置源故障标记，逐站兜底查询在 TTL 内也不再打网）。
        """
        if not coords:
            return
        clauses = "".join(
            f"node(around:{_RADIUS_M},{lat},{lng})[opening_hours];"
            f"way(around:{_RADIUS_M},{lat},{lng})[opening_hours];"
            for lat, lng in coords
        )
        query = f"[out:json];({clauses});out tags center;"
        try:
            response = self._client.post(self._url, data={"data": query}, timeout=30)
            response.raise_for_status()
            elements = response.json().get("elements") or []
        except (httpx.HTTPError, ValueError):
            _mark_source_down()
            return
        # 元素（node 带 lat/lon，way 带 center）→ 各站点取 200m 内最近的值
        points = []
        for element in elements:
            value = (element.get("tags") or {}).get("opening_hours")
            if not value:
                continue
            if element.get("type") == "node":
                plat, plng = element.get("lat"), element.get("lon")
            else:
                center = element.get("center") or {}
                plat, plng = center.get("lat"), center.get("lon")
            if plat is not None and plng is not None:
                points.append((plat, plng, value))
        for lat, lng in coords:
            best, best_d = None, float(_RADIUS_M)
            for plat, plng, value in points:
                d = haversine_km(lat, lng, plat, plng) * 1000
                if d <= best_d:
                    best, best_d = value, d
            _HOURS_CACHE[(lat, lng)] = best  # 未命中填 None（确定答案，不重查）
