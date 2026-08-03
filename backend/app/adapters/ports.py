"""Ports for external services.

Every external dependency (LLM provider, seichi data source, transit engine)
is accessed through one of these protocols. Tests and local development wire
fakes (app.adapters.fakes); live implementations arrive with later tickets.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass
class Seichi:
    """候选圣地：名称、坐标、对照截图引用、出处（集数+截图来源）。

    SeichiRepository 端口上的统一数据类型——fake 与 live（anitabi）适配器
    都返回它，API 边界的 schema 从它序列化，字段名不再泄漏数据源原始命名
    （anitabi 的 `s` 在这里是 `ep_seconds`）。
    """

    name: str
    lat: float
    lng: float
    id: str | None = None
    work: str | None = None
    area: str | None = None
    image: str | None = None  # 对照截图（缩略图 URL）
    ep: int | str | None = None  # 出处集数（可能为 "OST" 等）
    ep_seconds: int | None = None  # 截图在集内的时间（秒）
    origin: str | None = None  # 截图来源（CC BY-NC-SA 要求标注）
    origin_url: str | None = None


class LLMGateway(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str: ...


class SeichiRepository(Protocol):
    def search_seichi(self, work: str, area: str) -> list[Seichi]: ...


class TransitClient(Protocol):
    """交通查询端口（ADR-0003：OTP）。

    返回 dict 契约：{"mode", "duration_minutes", "fare_yen", "estimate"}——
    estimate=True 表示"没有真实数据"（fake/降级），调用方保留原估算段；
    estimate=False 表示真实查询结果，替换估算段。异常上抛由调用方降级。
    """

    def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        *,
        depart_at: datetime | None = None,
    ) -> dict[str, Any]: ...


class OpeningHoursSource(Protocol):
    """开放时间数据源（OSM opening_hours）。返回 None = 未知（不误标）。"""

    def opening_hours(self, lat: float, lng: float) -> str | None: ...
