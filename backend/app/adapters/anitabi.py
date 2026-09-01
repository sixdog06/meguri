"""anitabi.cn 公开数据 API 客户端 + 线上圣地仓库（ADR-0001，仅允许非商业使用）。

最终架构（用户拍板）：本地 JSON 只做 ID↔名字映射（Bangumi 离线灌库产物）；
运行时用映射拿 subjectID 后**实时**调 anitabi 拿圣地数据。anitabi 调用失败
（网络/超时/403/非 JSON 间隙页）抛 SeichiSourceUnavailable（API 映射 503，
**不降级本地数据包**）；anitabi 成功但无数据抛 NoSeichiData（显式区别于故障，
前端可见提示"这部作品没有圣地巡礼数据"）。

debug 模式（MEGURI_DEBUG_MODE=true，开发用）：完全不触 anitabi，lite/points
都返回固定罐头数据（K-ON! 1424 的京都切片，debug_anitabi_points.json），
其余逻辑（映射、过滤、错误区分）不变。
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from curl_cffi import requests as curl_requests
from curl_cffi.requests.exceptions import RequestException

from app.adapters.file_seichi import FileSeichiRepository
from app.adapters.ports import Seichi, SeichiRepository, WorkRef

ANITABI_BASE_URL = "https://api.anitabi.cn"


class _CurlCffiClient:
    """默认 HTTP 客户端：curl_cffi 伪装 Chrome 的 TLS 指纹。

    api.anitabi.cn 的 Cloudflare 按 TLS 指纹（JA3）封禁 httpx/requests——
    换 UA、加全套浏览器头都无效（本机实测同 IP 下 curl 200、httpx 403）。
    接口对齐 httpx.Client 的 .get(url, params) 子集，测试仍可注入
    httpx.MockTransport 回放客户端。
    """

    def __init__(self, timeout: float) -> None:
        self._timeout = timeout

    def get(self, url: str, params: dict[str, str] | None = None) -> Any:
        return curl_requests.get(
            url, params=params, impersonate="chrome", timeout=self._timeout
        )


class SeichiSourceUnavailable(Exception):
    """anitabi 不可达（网络/超时/403/间隙页）——显式故障，API 映射 503。"""


class NoSeichiData(Exception):
    """anitabi 正常响应但该作品没有巡礼数据——非故障，显式告知用户。"""


class InvalidAnitabiResponse(httpx.HTTPError):
    """anitabi 返回非合法 JSON（疑似 Cloudflare 200+HTML 间隙页）——按故障处理。"""


@dataclass
class AnitabiWorkSeichi:
    """单作品巡礼数据：作品名、主城市、圣地列表。"""

    work_name: str
    city: str
    seichi: list[Seichi]


def area_matches(area: str, city: str) -> bool:
    """城市名宽松匹配：用户说“宇治”应命中 anitabi 的“宇治市”。"""
    area, city = area.strip(), city.strip()
    return bool(area and city) and (area in city or city in area)


# 单点距作品主城市的上限：anitabi 数据偶有污染点（如 K-ON! 里混进柏林的
# 勃兰登堡门），超过即丢弃；200km 容得下"由良川橋"这类 ~65km 的日归点。
_MAX_POINT_DISTANCE_KM = 200.0


def _haversine_km(a: list[float], b: list[float]) -> float:
    """两个 [lat, lng] 的球面距离（km）。"""
    from math import asin, cos, radians, sin, sqrt

    lat1, lng1, lat2, lng2 = (radians(x) for x in (a[0], a[1], b[0], b[1]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


_DEBUG_POINTS_FILE = Path(__file__).with_name("debug_anitabi_points.json")


@lru_cache
def _debug_points() -> list[dict[str, Any]]:
    """debug 模式罐头地标（K-ON! 1424 真实响应快照）；进程内只读一次盘。"""
    return json.loads(_DEBUG_POINTS_FILE.read_text(encoding="utf-8"))


def _parse_json(response: Any) -> Any:
    """响应 JSON 解析；非 JSON（间隙页/HTML）抛 InvalidAnitabiResponse。

    ValueError 同时覆盖 stdlib 与 curl_cffi 的 JSONDecodeError（后者是
    ValueError 子类）。
    """
    try:
        return response.json()
    except ValueError as exc:
        raise InvalidAnitabiResponse(
            f"anitabi 返回非 JSON（HTTP {response.status_code}，疑似间隙页）"
        ) from exc


class AnitabiClient:
    """anitabi 底层客户端（构造可注入 HTTP 客户端便于回放测试）。

    debug=True 时完全不触网：lite/points 返回固定罐头数据（开发调试用，
    见模块头）；此时注入的 client 不会被使用。
    """

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        max_results: int = 60,
        client: Any = None,
        debug: bool = False,
    ) -> None:
        # client 可注入 httpx.MockTransport 客户端（测试回放）；默认走
        # _CurlCffiClient（Cloudflare 按 TLS 指纹封 httpx，见该类 docstring）
        self._client = client or _CurlCffiClient(timeout)
        self._max_results = max_results
        self._debug = debug

    def fetch_lite(self, subject_id: int) -> dict[str, Any] | None:
        """作品巡礼轻量信息；404 返回 None（无数据），网络/解析异常上抛。"""
        if self._debug:
            # 罐头 lite：cn 置空让 work_name 回退为用户查询名；城市/中心点
            # 固定京都（罐头 points 是 K-ON! 的京都切片；geo 供距离过滤）
            return {"cn": "", "city": "京都市", "geo": [35.0116, 135.7681]}
        response = self._client.get(f"{ANITABI_BASE_URL}/bangumi/{subject_id}/lite")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _parse_json(response)

    def fetch_points(self, subject_id: int) -> list[dict[str, Any]]:
        """全部巡礼地标（含截图）；网络/解析异常上抛。"""
        if self._debug:
            return _debug_points()
        response = self._client.get(
            f"{ANITABI_BASE_URL}/bangumi/{subject_id}/points/detail",
            params={"haveImage": "true"},
        )
        response.raise_for_status()
        data = _parse_json(response)
        if not isinstance(data, list):  # 错误 JSON 对象（如 {"error": ...}）按故障处理，不误判为"无数据"
            raise InvalidAnitabiResponse("anitabi points 返回非数组 JSON（疑似错误响应）")
        return data

    def fetch_image(self, url: str) -> bytes:
        """下载对照截图原始字节（image.anitabi.cn 同在 Cloudflare 后，需同一
        TLS 伪装通道）；灌库本地化用，失败上抛由调用方决定跳过。"""
        response = self._client.get(url)
        response.raise_for_status()
        return response.content

    def fetch_seichi(
        self, subject_id: int, work_fallback: str = ""
    ) -> AnitabiWorkSeichi | None:
        """lite + points → 单作品巡礼数据；无巡礼数据返回 None。"""
        lite = self.fetch_lite(subject_id)
        if lite is None:
            return None
        city = str(lite.get("city") or "")
        center = lite.get("geo")  # 作品主城市中心 [lat, lng]，供距离过滤
        work_name = str(lite.get("cn") or lite.get("title") or work_fallback)
        results: list[Seichi] = []
        for point in self.fetch_points(subject_id):
            geo = point.get("geo")
            if not geo:
                continue
            # 距主城市过远的点视为数据污染（如混进的海外点），丢弃
            if center and _haversine_km(center, geo) > _MAX_POINT_DISTANCE_KM:
                continue
            results.append(
                Seichi(
                    id=point.get("id"),
                    name=point.get("cn") or point.get("name") or "",
                    work=work_name,
                    area=city,
                    lat=geo[0],
                    lng=geo[1],
                    image=point.get("image"),
                    ep=point.get("ep"),
                    ep_seconds=point.get("s"),
                    origin=point.get("origin"),
                    origin_url=point.get("originURL"),
                )
            )
            if len(results) >= self._max_results:
                break
        return AnitabiWorkSeichi(work_name=work_name, city=city, seichi=results)


class AnitabiSeichiRepository:
    """SeichiRepository 的线上实现：本地映射解析 → anitabi 实时拉取。

    组合本地映射存储（resolve_works 主路径，FileSeichiRepository）与
    AnitabiClient；两种显式结果见模块头（SeichiSourceUnavailable /
    NoSeichiData）。anitabi 故障时按作品逐个回退本地离线包（显式
    fallback_notice 告知用户是离线数据，可能不是最新），可以降级，
    但绝不静默冒充实时数据。
    """

    def __init__(
        self,
        mapping: FileSeichiRepository,
        client: AnitabiClient | None = None,
        resolver: Any = None,
    ) -> None:
        self._mapping = mapping
        #: 作品名解析器：live 装配为 DbWorksResolver（works 表 + pg_trgm）；
        #: 缺省回退 mapping 自身（JSON 扫描，测试/离线用）
        self._resolver = resolver if resolver is not None else mapping
        self._client = client or AnitabiClient()
        #: 最近一次检索发生离线兜底时的用户可见提示（约定通道，tools 层读取
        #: 并进 notice）；无兜底保持 None。每次检索开头重置。
        self.fallback_notice: str | None = None
        #: 被地区过滤整个滤掉的作品摘要（约定通道，同 fallback_notice）。
        self.out_of_area: list[dict] = []

    def resolve_works(self, work: str) -> list[WorkRef]:
        """作品名 → 全部命中作品：经解析器（live=works 表，否则本地索引）。"""
        return self._resolver.resolve_works(work)

    def find_work(self, work: str) -> WorkRef | None:
        """作品名 → WorkRef：resolve_works 的首个命中（名字最短者）。"""
        refs = self._resolver.resolve_works(work)
        return refs[0] if refs else None

    def search_seichi(self, work: str, area: str) -> list[Seichi]:
        """解析全部命中作品 → 逐作品 anitabi 实时拉取 → 地区过滤 → 合并。

        未收录的作品返回 []（普通空结果）；全部命中作品都无巡礼数据抛
        NoSeichiData；anitabi 故障的作品优先回退本地离线包（显式 notice），
        全部失败且无任何结果才抛 SeichiSourceUnavailable。被地区过滤整个
        滤掉的作品记入 out_of_area（告知而非丢弃）。
        """
        self.fallback_notice = None
        self.out_of_area = []
        refs = self._resolver.resolve_works(work)  # 与 resolve_works/find_work 同一解析路径
        if not refs:
            return []

        results: list[Seichi] = []
        failures: list[Exception] = []  # anitabi 故障且无本地包可兜底的作品
        no_data: list[str] = []  # anitabi 正常但无巡礼数据的作品名
        for ref in refs:
            try:
                result = self._client.fetch_seichi(ref.subject_id, work)
            except (httpx.HTTPError, RequestException) as exc:  # httpx（测试回放）与 curl_cffi（线上默认）两类网络错误
                local = self._mapping.search_pack(ref.subject_id, area)
                if local:
                    self.fallback_notice = (
                        "实时圣地数据服务不可用，当前展示的是离线数据包（可能不是最新）"
                    )
                    results.extend(local)
                    continue
                failures.append(exc)
                continue
            if result is None or not result.seichi:
                no_data.append(ref.name)
                continue
            if area and not area_matches(area, result.city):
                self.out_of_area.append(
                    {"work": result.work_name, "city": result.city, "count": len(result.seichi)}
                )
                continue
            results.extend(result.seichi)

        if results:
            if failures:
                # 部分作品拉取失败且无兜底：结果可能不全，如实告知
                note = "部分作品的实时数据不可用，结果可能不全"
                self.fallback_notice = (
                    f"{self.fallback_notice}；{note}" if self.fallback_notice else note
                )
            return results
        if failures:
            raise SeichiSourceUnavailable(
                "圣地数据服务暂时不可用，请稍后重试"
            ) from failures[0]
        if no_data:
            names = "、".join(f"《{n}》" for n in no_data)
            raise NoSeichiData(f"{names}没有圣地巡礼数据")
        return []  # 全部命中作品都被地区滤掉（out_of_area 已记录）
