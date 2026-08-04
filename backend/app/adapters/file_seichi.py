"""SeichiRepository 的离线数据包实现（seichi_mode=file，应急于 anitabi 网络不可达）。

从本地 JSON 数据包读圣地数据（真实 anitabi 数据的离线切片）：
- data/seichi/index.json：作品名关键词 → bangumi subjectID
- data/seichi/<subjectID>.json：{subject_id, work, city, points: [...Seichi 字段]}

缺索引/缺文件一律优雅降级为空结果（不报错，与 live 实现的网络降级同语义）。
相对路径 data_dir 以仓库根目录解析（本文件上三级），cwd 无关。
"""

import json
from pathlib import Path

from app.adapters.ports import Seichi, WorkRef

_REPO_ROOT = Path(__file__).resolve().parents[3]


class FileSeichiRepository:
    def __init__(self, data_dir: str = "data/seichi") -> None:
        path = Path(data_dir)
        self._dir = path if path.is_absolute() else _REPO_ROOT / path

    def _load_index(self) -> dict[str, int]:
        """读作品索引（关键词→subjectID）；缺文件/坏 JSON 降级为空索引。"""
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

    def _match_subject(self, work: str) -> int | None:
        """按关键词包含关系匹配作品 subjectID；未收录返回 None。"""
        for keyword, subject_id in self._load_index().items():
            if keyword in work:
                return subject_id
        return None

    def find_work(self, work: str) -> WorkRef | None:
        """作品名 → WorkRef；未收录或数据文件缺失返回 None。"""
        subject_id = self._match_subject(work)
        if subject_id is None:
            return None
        data = self._load_work(subject_id)
        if data is None:
            return None
        return WorkRef(
            subject_id=subject_id,
            name=str(data.get("work") or work),
            city=str(data.get("city") or ""),
        )

    def search_seichi(self, work: str, area: str) -> list[Seichi]:
        """数据包内检索：关键词匹配作品 → 点列表按地区宽松过滤 → Seichi。"""
        subject_id = self._match_subject(work)
        if subject_id is None:
            return []
        data = self._load_work(subject_id)
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
