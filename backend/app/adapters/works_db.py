"""作品名解析的 DB 实现（live 模式主路径）：works 表 + pg_trgm，两跳匹配。

- 快速路径：norm 列（去空白）的 ILIKE 子串匹配，走 GIN trigram 索引——
  与旧 JSON 线性扫描语义一致（含空白忽略），但不再逐行扫；
- 模糊路径：子串无命中时按 pg_trgm similarity 取 top-5，sim ≥ 阈值者返回
  （错字容错；"京吹"这类无字面重合的俗名仍不命中，归属 LLM 归一化职责）。

file 模式（纯离线 demo）不走这里，保留 FileSeichiRepository 的 JSON 扫描。
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.adapters.ports import WorkRef

#: 模糊路径的相似度下限（% 运算符的阈值也按它设置）；按 eval 数据集标定
TRGM_MIN_SIMILARITY = 0.4
#: 模糊路径候选上限
_FUZZY_LIMIT = 5


def _escape_like(q: str) -> str:
    """ILIKE 模式串转义（用户输入里的 % _ \\ 不当通配符）。"""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class DbWorksResolver:
    """works 表查询器：只负责 作品名 → [WorkRef]，不碰圣地数据。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def resolve_works(self, work: str) -> list[WorkRef]:
        """作品名 → 全部命中（短名在前）；无命中返回空列表。"""
        work = work.strip()
        if not work:
            return []
        compact = "".join(work.split())
        refs = self._substring_match(compact)
        if refs:
            return refs
        return self._fuzzy_match(compact)

    def _substring_match(self, compact: str) -> list[WorkRef]:
        pattern = f"%{_escape_like(compact)}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT subject_id, name, name_cn FROM works"
                    " WHERE name_cn_norm ILIKE :pat ESCAPE '\\'"
                    "    OR name_norm ILIKE :pat ESCAPE '\\'"
                ),
                {"pat": pattern},
            ).all()
        # 名字短→长（精确命中排在衍生季之前）；同长保持 subject_id 序（确定性）
        rows = sorted(
            rows,
            key=lambda r: (min(len(r.name_cn or r.name), len(r.name)), r.subject_id),
        )
        return [
            WorkRef(subject_id=r.subject_id, name=r.name_cn or r.name, city="")
            for r in rows
        ]

    def _fuzzy_match(self, compact: str) -> list[WorkRef]:
        with self._engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
                {"t": str(TRGM_MIN_SIMILARITY)},
            )
            rows = conn.execute(
                text(
                    "SELECT subject_id, name, name_cn,"
                    " GREATEST(similarity(name_norm, :q),"
                    "         similarity(name_cn_norm, :q)) AS sim"
                    " FROM works WHERE name_norm % :q OR name_cn_norm % :q"
                    " ORDER BY sim DESC, subject_id LIMIT :limit"
                ),
                {"q": compact, "limit": _FUZZY_LIMIT},
            ).all()
        return [
            WorkRef(subject_id=r.subject_id, name=r.name_cn or r.name, city="")
            for r in rows
        ]
