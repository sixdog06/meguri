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

    def find_work(self, work: str) -> WorkRef | None:
        """作品名 → WorkRef：全量动画索引（name/name_cn 包含匹配，忽略空白）。

        空/纯空白输入直接返回 None（"" 包含于任何字符串，会误命中索引首条）。
        命中本地有数据包的作品时带上数据包的权威名与城市；否则 city 未知（空串）。
        """
        work = work.strip()
        if not work:
            return None
        compact = "".join(work.split())
        best: dict | None = None
        best_len = 0
        for item in self._load_works_index():
            # 命中该作品的全部名字（name_cn / name；忽略空白，"轻音少女第二季"
            # 也能命中带空格的 "轻音少女 第二季"）
            names = [item.get("name_cn") or "", item.get("name") or ""]
            hits = [
                n for n in names
                if n and (work in n or compact in "".join(n.split()))
            ]
            if not hits:
                continue
            shortest = min(len(n) for n in hits)
            # 多个作品名字都包含查询词时取名字最短的（"你的名字" 应命中
            # 《你的名字。》而非《…呼唤着你的名字》）；完全相等即最优，提前结束
            if best is None or shortest < best_len:
                best, best_len = item, shortest
                if shortest == len(work):
                    break
        if best is None:
            return None
        data = self._load_work(best["id"])
        return WorkRef(
            subject_id=best["id"],
            name=str(data.get("work")) if data else (best.get("name_cn") or best.get("name") or work),
            city=str(data.get("city") or "") if data else "",
        )

    def search_seichi(self, work: str, area: str) -> list[Seichi]:
        """数据包内检索：本地 ID 库解析 → 点列表按地区宽松过滤 → Seichi。"""
        ref = self.find_work(work)
        if ref is None:
            return []
        data = self._load_work(ref.subject_id)
        if data is None:
            return []
        work_name = str(data.get("work") or work)
        results = []
        for point in data.get("points", []):
            point_area = str(point.get("area") or data.get("city") or "")
            # 地区宽松匹配，与 live 实现语义一致
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
