"""OpeningHoursSource 的 live 实现：Overpass API 查 OSM opening_hours 标签。

按坐标就近取 200m 内带 opening_hours 的元素（圣地多为 OSM 已有 POI）。
查询失败/无数据一律返回 None（= 开放时间未知，不误标）。
"""

import httpx

_RADIUS_M = 200


class OverpassOpeningHours:
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
        query = (
            f"[out:json];(node(around:{_RADIUS_M},{lat},{lng})[opening_hours];"
            f"way(around:{_RADIUS_M},{lat},{lng})[opening_hours];);out tags 5;"
        )
        try:
            response = self._client.post(self._url, data={"data": query})
            response.raise_for_status()
            elements = response.json().get("elements") or []
        except (httpx.HTTPError, ValueError):
            return None
        for element in elements:
            value = (element.get("tags") or {}).get("opening_hours")
            if value:
                return value
        return None
