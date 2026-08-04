"""SeichiRepository 的离线数据包实现（seichi_mode=file；兼作 live 模式的 ID 映射源）。

本地 ID 库与圣地数据（全部为离线灌库产物，运行时不触网）：
- data/works/anime-1990plus.json：Bangumi 全量动画索引（作品 ID 空间，
  find_work 按 name/name_cn 关键词匹配——这是运行时唯一的作品名解析来源）；
- data/seichi/index.json：作品名别名 → bangumi subjectID（如“京吹”→115908）；
- data/seichi/<subjectID>.json：{subject_id, work, city, points: [...Seichi 字段]}。

缺索引/缺文件一律优雅降级为空结果（不报错）。相对路径以仓库根目录解析
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

    def _load_index(self) -> dict[str, int]:
        """读作品别名索引（关键词→subjectID）；缺文件/坏 JSON 降级为空索引。"""
        try:
            data = json.loads((self._dir / "index.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {k: v for k, v in data.items() if not k.startswith("_") and k != "comment"}

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

    def _match_subject(self, work: str) -> int | None:
        """别名索引关键词匹配（如“京吹”）；未命中返回 None。"""
        for keyword, subject_id in self._load_index().items():
            if keyword in work:
                return subject_id
        return None

    def find_work(self, work: str) -> WorkRef | None:
        """作品名 → WorkRef：先别名索引，再全量动画索引（name/name_cn 包含匹配）。

        空/纯空白输入直接返回 None（"" 包含于任何字符串，会误命中索引首条）。
        别名索引命中即返回（有无本地数据文件都返回——live 模式下由
        AnitabiSeichiRepository 实时拉取验证，别名不短路实时拉取）；全量索引命中的 city 未知（空串）。
        """
        work = work.strip()
        if not work:
            return None
        subject_id = self._match_subject(work)
        if subject_id is not None:
            data = self._load_work(subject_id)
            return WorkRef(
                subject_id=subject_id,
                name=str(data.get("work") or work) if data else work,
                city=str(data.get("city") or "") if data else "",
            )
        for item in self._load_works_index():
            if work in (item.get("name_cn") or "") or work in (item.get("name") or ""):
                return WorkRef(
                    subject_id=item["id"],
                    name=item.get("name_cn") or item.get("name") or work,
                    city="",
                )
        return None

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
