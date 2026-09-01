"""works 表灌库：data/works/anime-1990plus.json（Bangumi 离线索引，Git 里的源
artifact）→ DB 服务层。幂等 upsert（按 subject_id merge），可反复跑。

用法：.venv/bin/python -m app.rag.ingest_works
（从仓库根运行，读 .env.local 的 MEGURI_DATABASE_URL；或显式传环境变量）
"""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import _get_engine
from app.models import WorksRow

# 本文件上三级 = 仓库根（backend/app/rag/ingest_works.py → rag/app/backend → 根）
_WORKS_INDEX_PATH = Path(__file__).resolve().parents[3] / "data/works/anime-1990plus.json"


def _norm(name: str) -> str:
    """去空白归一化（"轻音少女 第二季" → "轻音少女第二季"）；trigram 索引建在
    norm 列上，查询侧同样归一化，空白差异不影响匹配。"""
    return "".join(name.split())


def load_works(path: Path = _WORKS_INDEX_PATH) -> int:
    """JSON → works 表（批量 upsert，幂等）；返回写入条数。"""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    items = json.loads(path.read_text(encoding="utf-8"))
    rows = [
        {
            "subject_id": item["id"],
            "name": (name := str(item.get("name") or "")),
            "name_cn": (name_cn := str(item.get("name_cn") or "")),
            "name_norm": _norm(name),
            "name_cn_norm": _norm(name_cn),
            "air_date": str(item.get("air_date") or ""),
            "summary": str(item.get("summary") or ""),
        }
        for item in items
    ]
    stmt = pg_insert(WorksRow)
    stmt = stmt.on_conflict_do_update(
        index_elements=["subject_id"],
        set_={c: getattr(stmt.excluded, c) for c in rows[0] if c != "subject_id"},
    )
    with Session(_get_engine()) as session:
        # 分批防单条语句参数过多（每行 7 参数，PG 上限 65535）
        for i in range(0, len(rows), 5000):
            session.execute(stmt, rows[i : i + 5000])
        session.commit()
    return len(rows)


def main() -> None:
    count = load_works()
    print(f"已灌入 {count} 条作品记录 → works 表")


if __name__ == "__main__":
    main()
