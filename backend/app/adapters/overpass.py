"""OpeningHoursSource 的 live 实现：Overpass API 查 OSM opening_hours 标签。

按坐标就近取 200m 内带 opening_hours 的元素（圣地多为 OSM 已有 POI）。
查询失败/无数据一律返回 None（= 开放时间未知，不误标）。
"""

import httpx

_RADIUS_M = 200

# 进程级缓存（与 otp._ROUTE_CACHE 同语义）：编辑后的 revalidate 重查全行程
# 的开放时间，Overpass 是远程限速 API，无缓存时每次编辑分钟级——缓存后秒级。
_HOURS_CACHE: dict[tuple[float, float], str | None] = {}


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

        结果按坐标进程内缓存（含 None——无数据也是确定答案，不重查）。
        """
        key = (lat, lng)
        if key in _HOURS_CACHE:
            return _HOURS_CACHE[key]
        query = (
            f"[out:json];(node(around:{_RADIUS_M},{lat},{lng})[opening_hours];"
            f"way(around:{_RADIUS_M},{lat},{lng})[opening_hours];);out tags 5;"
        )
        try:
            response = self._client.post(self._url, data={"data": query})
            response.raise_for_status()
            elements = response.json().get("elements") or []
        except (httpx.HTTPError, ValueError):
            return None  # 故障不缓存：下次重试（与"无数据"区分）
        value = None
        for element in elements:
            value = (element.get("tags") or {}).get("opening_hours")
            if value:
                break
        _HOURS_CACHE[key] = value or None
        return value or None
