"""RAG 语料灌库（#8）：本地作品索引 summary + anitabi 地标 → CorpusStore。

语料事实（如实记录）：
- 作品简介读本地 data/works/anime-1990plus.json（Bangumi 离线灌库产物，
  含 summary 字段，运行时不触 bgm.tv）；
- anitabi /points/detail **没有地标自由文本/评论字段**（只有名称、集数、
  坐标、截图、来源）——anitabi 语料是"元数据文本化"（名称+出处集数拼句），
  不是地标描述原文。
- anitabi 数据全程经 SeichiRepository 公开接口（find_work / search_seichi，
  ADR-0001），不直调数据源、不碰私有方法。

幂等（chunk id 稳定，upsert 覆盖）；离线/网络正常处都能跑——本机 IP 被
Cloudflare 封在 api.anitabi.cn 时 anitabi 部分会取不到。

用法：
  MEGURI_CORPUS_MODE=live MEGURI_DATABASE_URL=... \
    .venv/bin/python -m app.rag.ingest --work 吹响吧！上低音号
"""

import argparse
import json
from pathlib import Path

from app.adapters.ports import CorpusChunk, CorpusStore, Seichi, SeichiRepository
from app.adapters.providers import get_corpus_store, get_seichi_repository


def chunks_from_bangumi_subject(subject: dict) -> list[CorpusChunk]:
    """bgm.tv /v0/subjects/{id} 响应 → 作品条目语料块（真实简介文本）。"""
    name_cn = subject.get("name_cn") or subject.get("name") or ""
    name = subject.get("name") or ""
    summary = (subject.get("summary") or "").strip()
    return [
        CorpusChunk(
            id=f"bangumi:{subject['id']}",
            source="bangumi.tv",
            work=name_cn or name,
            text=f"《{name_cn}》（{name}）：{summary}" if summary else f"《{name_cn}》（{name}）",
        )
    ]


def chunks_from_seichi(seichi: list[Seichi], subject_id: int | None = None) -> list[CorpusChunk]:
    """Seichi 列表（repository 公开接口产出）→ 地标语料块（元数据文本化）。"""
    chunks = []
    for s in seichi:
        ep = s.ep
        ep_text = f"第{ep}集" if isinstance(ep, int) else (str(ep) if ep else "")
        text = f"《{s.work}》取景地「{s.name}」"
        text += f"，出自{ep_text}。" if ep_text else "。"
        point_id = f"{subject_id}:{s.id}" if subject_id is not None else str(s.id)
        chunks.append(
            CorpusChunk(
                id=f"anitabi:{point_id}",
                source="anitabi",
                work=s.work or "",
                text=text,
            )
        )
    return chunks


def collect_chunks(work: str, max_points: int = 200) -> list[CorpusChunk]:
    """抓取某作品的全部语料（本地作品索引的 summary + anitabi 地标）。

    作品简介读本地 data/works/anime-1990plus.json（离线灌库已含 summary，
    与数据层架构一致，不再实时调 bgm.tv）。
    """
    repo: SeichiRepository = get_seichi_repository()
    work_ref = repo.find_work(work)
    if work_ref is None:
        raise SystemExit(f"找不到作品巡礼数据：{work}")

    subject = _find_in_works_index(work_ref.subject_id)
    chunks = chunks_from_bangumi_subject(subject) if subject else []

    seichi = repo.search_seichi(work, "")[:max_points]
    chunks += chunks_from_seichi(seichi, subject_id=work_ref.subject_id)
    return chunks


_WORKS_INDEX_PATH = Path(__file__).resolve().parents[2] / "data/works/anime-1990plus.json"


def _find_in_works_index(subject_id: int) -> dict | None:
    """本地全量动画索引按 id 查条目；缺文件/未收录返回 None（语料缺简介不致命）。"""
    try:
        works = json.loads(_WORKS_INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return next((w for w in works if w.get("id") == subject_id), None)


def main() -> None:
    """CLI 入口：python -m app.rag.ingest --work <作品名>（幂等灌库）。"""
    parser = argparse.ArgumentParser(description="灌 RAG 语料进 CorpusStore（幂等）")
    parser.add_argument("--work", required=True, help="作品名（如 吹响吧！上低音号）")
    parser.add_argument("--max-points", type=int, default=200)
    args = parser.parse_args()

    chunks = collect_chunks(args.work, max_points=args.max_points)
    store: CorpusStore = get_corpus_store()
    store.upsert(chunks)
    print(f"已灌入 {len(chunks)} 个语料块（{args.work}）")


if __name__ == "__main__":
    main()
