"""Storyteller（#8，CONTEXT.md：讲解角色）。

语料库已下线（corpus_chunks 表已删）：anitabi 地标没有自由文本字段，此前
"元数据文本化"的语料是把 planner 已有的站点数据拼成句子绕一圈再检索回来，
不产生新信息。讲解的事实依据改为**站点自带元数据**（作品名/站名/出处集数/
截图秒数），citation = anitabi 截图来源署名（origin/origin_url——CC BY-NC-SA
本来就要求标注来源）。

两种模式：
- 模板拼装（无 LLM）：元数据拼句；
- 生成式（接入真实 LLM）：只许用给定元数据写作 ≤100 字；失败回退模板。
生成式的约束是软性的（prompt 层"不得编造"），不再有"检索不到就不产出"
的硬闸门——这是删语料库时用户拍板接受的取舍。
"""

import logging
from dataclasses import dataclass

import httpx
from openai import APIError

from app.adapters.llm import LLMUnavailableError
from app.adapters.ports import LLMGateway, Seichi
from app.agents.planner import ItinerarySnapshot, Progress

logger = logging.getLogger(__name__)


@dataclass
class Citation:
    """讲解的来源署名：anitabi 截图的 origin（来源名）与 origin_url。"""

    source: str
    url: str | None = None


@dataclass
class Narration:
    """单站讲解：文本 + 来源署名；citation=None = 站点无来源信息。"""

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


def _template_text(stop: Seichi) -> str:
    """元数据拼句（拼装模式/生成失败兜底）：作品名 + 站名 + 出处集数。"""
    text = f"《{stop.work}》取景地「{stop.name}」"
    if stop.ep:
        ep_text = f"第{stop.ep}集" if isinstance(stop.ep, int) else str(stop.ep)
        text += f"，出自{ep_text}"
        if stop.ep_seconds:
            text += f"（约 {stop.ep_seconds // 60} 分 {stop.ep_seconds % 60} 秒处）"
    return text + "。"


def _generate(llm: LLMGateway, stop: Seichi) -> str:
    """生成式讲解：只许用给定元数据；失败回退模板拼句。"""
    facts = _template_text(stop)
    try:
        raw = llm.complete(
            [
                {
                    "role": "system",
                    "content": "你是动画圣地巡礼的讲解撰写者。只能依据给定事实写作，"
                    "不得编造任何场景或情节；事实很少就把句子写短。",
                },
                {
                    "role": "user",
                    "content": (
                        f"为圣地「{stop.name}」写一段不超过 100 字的中文讲解。"
                        f"已知事实只有：{facts}"
                        "要求：自然一段连贯文字，不用列表，不要提及“事实”二字。"
                    ),
                },
            ]
        )
    except (httpx.HTTPError, APIError, LLMUnavailableError) as exc:
        # 预期的 LLM/网络错误：回退模板拼句；编程错误照常抛出
        logger.warning("生成式讲解失败，回退模板拼装: %s: %s", type(exc).__name__, exc)
        return facts
    text = raw.strip().strip('"').strip()
    return text if text else facts


def _citation_of(stop: Seichi) -> Citation | None:
    """来源署名：站点带 origin（截图来源）才有 citation；没有就如实为空。"""
    if not stop.origin:
        return None
    return Citation(source=stop.origin, url=stop.origin_url)


def narrate_itinerary(
    snapshot: ItinerarySnapshot,
    *,
    progress: Progress | None = None,
    existing: dict[str, Narration] | None = None,
    llm: LLMGateway | None = None,
) -> ItinerarySnapshot:
    """就地给每个圣地附加讲解（day.narrations），返回 snapshot。

    existing（编辑流程用）：按 seichi_id 保留已有讲解，只给新加入的站生成。
    llm 提供时走生成式讲解（接真实模型；fake 模式保持模板拼装）。
    """
    emit = progress or (lambda stage: None)
    emit("讲解中")

    for day in snapshot.days:
        for stop in day.seichi:
            seichi_id = str(stop.id)
            if existing is not None and seichi_id in existing:
                day.narrations.append(existing[seichi_id])
                continue
            text = _generate(llm, stop) if llm is not None else _template_text(stop)
            day.narrations.append(
                Narration(
                    seichi_id=seichi_id,
                    text=text,
                    citation=_citation_of(stop),
                )
            )
    return snapshot
