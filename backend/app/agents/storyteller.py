"""Storyteller（#8，CONTEXT.md：讲解角色）。

无真 LLM 时的检索式拼装：为行程中每个圣地经 CorpusStore 统一检索接口取
top-1 语料，以原文片段 + citation 模板化成讲解——讲解必然引用检索到的
语料，绝不自由发挥（检索不到/不达标就不产出讲解）。接真 LLM 后改为基于
检索语料的生成式讲解，citation 契约保持不变。
"""

from dataclasses import dataclass

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.adapters.ports import CorpusStore
from app.agents.planner import ItinerarySnapshot, Progress

_EXCERPT_LEN = 120


@dataclass
class Citation:
    """讲解引用的语料出处（与 ports.CorpusChunk 风格一致）。"""

    chunk_id: str
    source: str


@dataclass
class Narration:
    """单站讲解：检索语料原文片段 + citation；citation=None = 未检索到语料。"""

    seichi_id: str
    text: str
    citation: Citation | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Narration":
        """落库的 asdict 结构 → dataclass（字段知识只此一处，加字段不漂移）。"""
        citation = data.get("citation")
        return cls(
            seichi_id=data["seichi_id"],
            text=data["text"],
            citation=Citation(**citation) if citation else None,
        )


def narrate_itinerary(
    snapshot: ItinerarySnapshot,
    corpus: CorpusStore,
    *,
    progress: Progress | None = None,
    existing: dict[str, Narration] | None = None,
) -> ItinerarySnapshot:
    """就地给每个圣地附加讲解（day.narrations），返回 snapshot。

    existing（编辑流程用）：按 seichi_id 保留已有讲解，只给新加入的站检索。
    """
    emit = progress or (lambda stage: None)
    emit("讲解中")

    for day in snapshot.days:
        for stop in day.seichi:
            seichi_id = str(stop.id)
            if existing is not None and seichi_id in existing:
                day.narrations.append(existing[seichi_id])
                continue
            query = f"{snapshot.work or ''} {stop.name}".strip()
            try:
                chunks = corpus.search(query, k=1)
            except (SQLAlchemyError, httpx.HTTPError):
                chunks = []  # 语料库（DB/网络）不可达不拖垮行程生成；编程错误照常抛
            if not chunks:
                continue
            top = chunks[0]
            excerpt = top.text[:_EXCERPT_LEN] + ("…" if len(top.text) > _EXCERPT_LEN else "")
            day.narrations.append(
                Narration(
                    seichi_id=seichi_id,
                    text=excerpt,
                    citation=Citation(chunk_id=top.id, source=top.source),
                )
            )
    return snapshot
