"""SeichiRepository 的离线数据包实现（seichi_mode=file；兼作 live 模式的 ID 映射源）。

本地 ID 库与圣地数据（全部为离线灌库产物，运行时不触网）：
- data/works/anime-1990plus.json：Bangumi 全量动画索引（作品 ID 空间，
  find_work 按 name/name_cn 匹配——这是运行时唯一的作品名解析来源）；
- data/seichi/<subjectID>.json：{subject_id, work, city, points: [...Seichi 字段]}。

缺文件一律优雅降级为空结果（不报错）。相对路径以仓库根目录解析
（本文件上三级），cwd 无关。
"""

import json
from pathlib import Path

from app.adapters.ports import Seichi, WorkRef

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WORKS_FILE = "data/works/anime-1990plus.json"

# 全量作品索引缓存：path → (mtime, data)。9MB JSON 只读一次，mtime 变化
# （重新灌库）才重读；stat 每次调用只做一次，开销可忽略。
_WORKS_CACHE: dict[str, tuple[float, list[dict]]] = {}


class FileSeichiRepository:
    def __init__(self, data_dir: str = "data/seichi", works_file: str = DEFAULT_WORKS_FILE) -> None:
        path = Path(data_dir)
        self._dir = path if path.is_absolute() else _REPO_ROOT / path
        works_path = Path(works_file)
        self._works_file = works_path if works_path.is_absolute() else _REPO_ROOT / works_path
        #: 最近一次检索被地区过滤整个滤掉的作品摘要（约定通道，tools 层读取
        #: 并告知用户）；无则空列表。每次检索开头重置。
        self.out_of_area: list[dict] = []

    def _load_work(self, subject_id: int) -> dict | None:
        """读某作品数据文件；不存在/坏 JSON 返回 None（优雅降级）。"""
        try:
            return json.loads((self._dir / f"{subject_id}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _load_works_index(self) -> list[dict]:
        """读 Bangumi 全量动画索引（进程内缓存，mtime 变化才重读）；
        缺文件/坏 JSON 降级为空列表。"""
        key = str(self._works_file)
        try:
            mtime = self._works_file.stat().st_mtime
        except OSError:
            return []
        cached = _WORKS_CACHE.get(key)
        if cached is None or cached[0] != mtime:
            try:
                cached = (mtime, json.loads(self._works_file.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                return []
            _WORKS_CACHE[key] = cached
        return cached[1]

    def resolve_works(self, work: str) -> list[WorkRef]:
        """作品名 → 全部命中作品：全量动画索引（name/name_cn 包含匹配，忽略空白）。

        多命中全部保留（"轻音少女" → 第一季/第二季/剧场版），按名字短→长排序；
        空/纯空白输入直接返回空（"" 包含于任何字符串，会误命中全部）。
        命中本地有数据包的作品时带上数据包的权威名与城市；否则 city 未知（空串）。
        """
        work = work.strip()
        if not work:
            return []
        compact = "".join(work.split())
        matched: list[tuple[int, dict]] = []  # (最短命中名长, item)
        for item in self._load_works_index():
            # 命中该作品的全部名字（name_cn / name；忽略空白，"轻音少女第二季"
            # 也能命中带空格的 "轻音少女 第二季"）
            names = [item.get("name_cn") or "", item.get("name") or ""]
            hits = [
                n for n in names
                if n and (work in n or compact in "".join(n.split()))
            ]
            if hits:
                matched.append((min(len(n) for n in hits), item))
        # 名字短的在前：精确命中（"轻音少女"）排在衍生季（"轻音少女 第二季"）之前
        matched.sort(key=lambda pair: pair[0])
        refs = []
        for _, item in matched:
            data = self._load_work(item["id"])
            refs.append(WorkRef(
                subject_id=item["id"],
                name=str(data.get("work")) if data else (item.get("name_cn") or item.get("name") or work),
                city=str(data.get("city") or "") if data else "",
            ))
        return refs

    def find_work(self, work: str) -> WorkRef | None:
        """单个作品解析：resolve_works 的首个命中（名字最短者）；无命中返回 None。"""
        refs = self.resolve_works(work)
        return refs[0] if refs else None

    def search_seichi(self, work: str, area: str) -> list[Seichi]:
        """数据包内检索：解析全部命中作品 → 逐包取点按地区宽松过滤 → 合并。

        被地区过滤整个滤掉的作品记入 out_of_area（约定通道，tools 层读取
        并如实告知用户"还有这些地方的点，本次未包含"），不静默丢弃。
        """
        self.out_of_area = []
        results: list[Seichi] = []
        for ref in self.resolve_works(work):
            data = self._load_work(ref.subject_id)
            if data is None:
                continue
            work_name = str(data.get("work") or work)
            city = str(data.get("city") or "")
            points = data.get("points", [])
            in_area = self._pack_points(data, work_name, city, area)
            results.extend(in_area)
            # 区域外摘要：该区域一点没剩下但作品本身有点 → 告知而非丢弃
            if area and points and not in_area:
                self.out_of_area.append(
                    {"work": work_name, "city": city, "count": len(points)}
                )
        return results

    def search_pack(self, subject_id: int, area: str) -> list[Seichi]:
        """单个离线包检索（live 模式 anitabi 故障时的 per-work 兜底用）；
        包不存在/为空返回 []。不触碰 out_of_area（由调用方聚合）。"""
        data = self._load_work(subject_id)
        if data is None:
            return []
        work_name = str(data.get("work") or "")
        city = str(data.get("city") or "")
        return self._pack_points(data, work_name, city, area)

    @staticmethod
    def _pack_points(data: dict, work_name: str, city: str, area: str) -> list[Seichi]:
        """单包点列表 → Seichi（地区宽松匹配，与 live 实现语义一致）。"""
        results = []
        for point in data.get("points", []):
            point_area = str(point.get("area") or city)
            if area and not (area in point_area or point_area in area):
                continue
            results.append(
                Seichi(
                    id=point.get("id"),
                    name=point.get("name") or "",
                    work=work_name,
                    area=point_area,
                    lat=point["lat"],
                    lng=point["lng"],
                    image=point.get("image"),
                    ep=point.get("ep"),
                    ep_seconds=point.get("ep_seconds"),
                    origin=point.get("origin"),
                    origin_url=point.get("origin_url"),
                )
            )
        return results
