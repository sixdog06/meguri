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


def narrate_itinerary(
    snapshot: ItinerarySnapshot,
    corpus: CorpusStore,
    *,
    progress: Progress | None = None,
) -> ItinerarySnapshot:
    """就地给每个圣地附加讲解（day.narrations），返回 snapshot。"""
    emit = progress or (lambda stage: None)
    emit("讲解中")

    for day in snapshot.days:
        for stop in day.seichi:
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
                    seichi_id=str(stop.id),
                    text=excerpt,
                    citation=Citation(chunk_id=top.id, source=top.source),
                )
            )
    return snapshot
