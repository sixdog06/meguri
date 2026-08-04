"""Storyteller（#8，CONTEXT.md：讲解角色）。

两种模式：
- 检索式拼装（fake/默认）：top-1 语料原文片段 + citation 模板化；
- 生成式（接入真实 LLM 后）：检索 chunks 作为上下文，让 LLM 写一段
  ≤100 字讲解；citation 仍取检索 top-1（确定性，不由模型编造）。
零幻觉底线：检索不到语料就不产出讲解（两种模式一致）。LLM 调用失败
回退检索式拼装（记日志）。
"""

import logging
from dataclasses import dataclass

import httpx
from openai import APIError
from sqlalchemy.exc import SQLAlchemyError

from app.adapters.ports import CorpusStore, LLMGateway
from app.agents.planner import ItinerarySnapshot, Progress

logger = logging.getLogger(__name__)

_EXCERPT_LEN = 120
_GENERATIVE_MAX_CHUNKS = 3


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


def _excerpt(text: str) -> str:
    return text[:_EXCERPT_LEN] + ("…" if len(text) > _EXCERPT_LEN else "")


def _generate(
    llm: LLMGateway, work: str, stop_name: str, chunks: list
) -> str:
    """生成式讲解：检索 chunks 为唯一依据，LLM 写 ≤100 字；失败回退摘录。"""
    context = "\n".join(
        f"{i + 1}.（来源：{c.source}）{c.text}" for i, c in enumerate(chunks)
    )
    try:
        raw = llm.complete(
            [
                {
                    "role": "system",
                    "content": "你是动画圣地巡礼的讲解撰写者。只能依据给定资料写作，"
                    "不得编造任何事实；资料不足就写得简短。",
                },
                {
                    "role": "user",
                    "content": (
                        f"为圣地「{stop_name}」（作品《{work}》）写一段不超过 100 字的"
                        f"中文讲解，涵盖作品关联与名场面。可用资料：\n{context}\n"
                        "要求：自然一段连贯文字，不用列表，不要提及“资料”二字。"
                    ),
                },
            ]
        )
    except (httpx.HTTPError, APIError) as exc:
        # 预期的 LLM/网络错误：回退检索式拼装；编程错误照常抛出
        logger.warning("生成式讲解失败，回退摘录拼装: %s: %s", type(exc).__name__, exc)
        return _excerpt(chunks[0].text)
    text = raw.strip().strip('"').strip()
    return text if text else _excerpt(chunks[0].text)


def narrate_itinerary(
    snapshot: ItinerarySnapshot,
    corpus: CorpusStore,
    *,
    progress: Progress | None = None,
    existing: dict[str, Narration] | None = None,
    llm: LLMGateway | None = None,
) -> ItinerarySnapshot:
    """就地给每个圣地附加讲解（day.narrations），返回 snapshot。

    existing（编辑流程用）：按 seichi_id 保留已有讲解，只给新加入的站检索。
    llm 提供时走生成式讲解（接真实模型；fake 模式保持检索式拼装）。
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
                chunks = corpus.search(query, k=_GENERATIVE_MAX_CHUNKS if llm else 1)
            except (SQLAlchemyError, httpx.HTTPError):
                chunks = []  # 语料库（DB/网络）不可达不拖垮行程生成；编程错误照常抛
            if not chunks:
                continue
            top = chunks[0]
            # 已知间隙（仅记录不改）：生成式用 top-3 chunks 作上下文但只 cite
            # top-1——讲解可能用了 chunk 2-3 的事实而只引 chunk 1（后续可改多 citation）
            if llm is not None:
                text = _generate(llm, snapshot.work or "", stop.name, chunks)
            else:
                text = _excerpt(top.text)
            day.narrations.append(
                Narration(
                    seichi_id=seichi_id,
                    text=text,
                    citation=Citation(chunk_id=top.id, source=top.source),
                )
            )
    return snapshot
