"""SeichiRepository 的 live 实现：anitabi.cn 公开数据 API（ADR-0001）。

仅允许非商业使用（CC BY-NC-SA 4.0），见 ADR-0001。

用到的端点（https://navi.anitabi.cn/docs/api/）：
- POST https://api.bgm.tv/v0/search/subjects
  作品名 → bangumi subjectID（anitabi 的作品 id 即 bangumi subjectID；
  anitabi 公开 API 没有按名搜索作品的端点，故经 bangumi.tv 解析）
- GET  https://api.anitabi.cn/bangumi/{subjectID}/lite
  作品巡礼信息（城市 city、概览坐标等）
- GET  https://api.anitabi.cn/bangumi/{subjectID}/points/detail?haveImage=true
  全部巡礼地标（名称、geo 坐标、对照截图 image、出处 ep/s、截图来源 origin/originURL）

网络故障一律降级为空结果，不拖垮对话主流程。
"""

from typing import Any

import httpx

from app.adapters.ports import Seichi, WorkRef

BANGUMI_SEARCH_URL = "https://api.bgm.tv/v0/search/subjects"
ANITABI_BASE_URL = "https://api.anitabi.cn"

# bangumi.tv 要求带可识别的 User-Agent
USER_AGENT = "sixdog06/meguri (https://github.com/sixdog06/meguri)"


class AnitabiSeichiRepository:
    """SeichiRepository 的 anitabi 在线实现（构造可注入 httpx.Client 便于回放测试）。"""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        max_works: int = 3,
        max_results: int = 60,
        client: httpx.Client | None = None,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": USER_AGENT}
        )
        self._max_works = max_works
        self._max_results = max_results

    def find_work(self, work: str) -> WorkRef | None:
        """作品名 → subjectID/名称/主城市（首个匹配且有巡礼数据的作品）。"""
        for subject_id in self._resolve_subject_ids(work):
            lite = self._fetch_lite(subject_id)
            if lite is None:
                continue
            return WorkRef(
                subject_id=subject_id,
                name=str(lite.get("cn") or lite.get("title") or work),
                city=str(lite.get("city") or ""),
            )
        return None

    def search_seichi(self, work: str, area: str) -> list[Seichi]:
        """作品名解析 → 逐作品拉巡礼地标，按地区过滤后归一为 Seichi 列表。

        单作品失败（无巡礼数据/网络异常）跳过不影响其它作品；上限 max_results。
        """
        results: list[Seichi] = []
        for subject_id in self._resolve_subject_ids(work):
            lite = self._fetch_lite(subject_id)
            if lite is None:
                continue  # 该作品在 anitabi 没有巡礼数据
            city = str(lite.get("city") or "")
            if area and not self._area_matches(area, city):
                continue
            work_name = str(lite.get("cn") or lite.get("title") or work)
            for point in self._fetch_points(subject_id):
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
                    return results
        return results

    @staticmethod
    def _area_matches(area: str, city: str) -> bool:
        """城市名宽松匹配：用户说“宇治”应命中 anitabi 的“宇治市”。"""
        area, city = area.strip(), city.strip()
        return bool(area and city) and (area in city or city in area)

    def _resolve_subject_ids(self, work: str) -> list[int]:
        """bgm.tv 搜索动画条目，取前 max_works 个 subjectID；故障降级为空。"""
        try:
            response = self._client.post(
                BANGUMI_SEARCH_URL,
                json={"keyword": work, "filter": {"type": [2]}},  # type 2 = 动画
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        return [item["id"] for item in data.get("data", [])[: self._max_works]]

    def _fetch_lite(self, subject_id: int) -> dict[str, Any] | None:
        """anitabi 作品巡礼轻量信息（城市等）；404/故障返回 None（无数据）。"""
        try:
            response = self._client.get(f"{ANITABI_BASE_URL}/bangumi/{subject_id}/lite")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            return None

    def _fetch_points(self, subject_id: int) -> list[dict[str, Any]]:
        """anitabi 全部巡礼地标（含截图）；故障降级为空列表。"""
        try:
            response = self._client.get(
                f"{ANITABI_BASE_URL}/bangumi/{subject_id}/points/detail",
                params={"haveImage": "true"},
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        return data if isinstance(data, list) else []
