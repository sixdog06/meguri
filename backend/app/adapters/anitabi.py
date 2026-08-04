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
            # 罐头 lite：cn 置空让 work_name 回退为用户查询名；城市固定京都
            # （罐头 points 主要是 K-ON! 的京都切片）
            return {"cn": "", "city": "京都市"}
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

    def fetch_seichi(
        self, subject_id: int, work_fallback: str = ""
    ) -> AnitabiWorkSeichi | None:
        """lite + points → 单作品巡礼数据；无巡礼数据返回 None。"""
        lite = self.fetch_lite(subject_id)
        if lite is None:
            return None
        city = str(lite.get("city") or "")
        work_name = str(lite.get("cn") or lite.get("title") or work_fallback)
        results: list[Seichi] = []
        for point in self.fetch_points(subject_id):
            geo = point.get("geo")
            if not geo:
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

    组合本地映射存储（find_work 主路径，FileSeichiRepository）与
    AnitabiClient；两种显式结果见模块头（SeichiSourceUnavailable /
    NoSeichiData）。
    """

    def __init__(self, mapping: SeichiRepository, client: AnitabiClient | None = None) -> None:
        self._mapping = mapping
        self._client = client or AnitabiClient()

    def find_work(self, work: str) -> WorkRef | None:
        """作品名 → WorkRef：只查本地 ID↔名字映射（运行时不调 Bangumi）。"""
        return self._mapping.find_work(work)

    def search_seichi(self, work: str, area: str) -> list[Seichi]:
        """本地映射解析 → anitabi 实时拉取，按地区过滤。

        未收录的作品返回 []（普通空结果）；anitabi 故障抛
        SeichiSourceUnavailable；anitabi 无该作品数据抛 NoSeichiData。
        """
        ref = self._mapping.find_work(work)
        if ref is None:
            return []
        try:
            result = self._client.fetch_seichi(ref.subject_id, work)
        except (httpx.HTTPError, RequestException) as exc:  # httpx（测试回放）与 curl_cffi（线上默认）两类网络错误
            raise SeichiSourceUnavailable(
                "圣地数据服务暂时不可用，请稍后重试"
            ) from exc
        if result is None or not result.seichi:
            raise NoSeichiData(f"《{ref.name}》没有圣地巡礼数据")
        if area and not area_matches(area, result.city):
            return []
        return result.seichi
