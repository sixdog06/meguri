"""Ticket #8：CorpusStore 检索接口与 ingestion 解析。

- PgVectorCorpusStore：真实 Postgres（5433）+ pgvector 余弦检索 + HashEmbedding；
- ingestion 解析函数：bgm.tv 条目 / anitabi 地标 JSON fixture → CorpusChunk。
"""

import pytest

from app.adapters.ports import CorpusChunk, Seichi
from app.db import _get_engine
from app.rag.embedding import HashEmbeddingProvider
from app.rag.ingest import chunks_from_bangumi_subject, chunks_from_seichi
from app.rag.store import PgVectorCorpusStore

# --- pgvector 检索（真实 db，conftest 已建表 + vector 扩展） ---

CORPUS = [
    CorpusChunk(id="c1", source="anitabi", work="吹响吧！上低音号",
                text="宇治桥是久美子放学路过的桥，桥下是宇治川。"),
    CorpusChunk(id="c2", source="anitabi", work="吹响吧！上低音号",
                text="大吉山展望台是丽奈与久美子夜登山吹奏的地方。"),
    CorpusChunk(id="c3", source="bangumi.tv", work="轻音少女",
                text="丰乡小学校旧址是轻音少女社团活动室原型。"),
]


@pytest.fixture()
def store():
    store = PgVectorCorpusStore(_get_engine(), HashEmbeddingProvider())
    store.upsert(CORPUS)
    return store


def test_pgvector检索返回相关chunk(store):
    results = store.search("宇治桥 久美子 放学 路过 宇治川", k=2)

    assert results[0].id == "c1"
    assert {c.id for c in results} <= {"c1", "c2", "c3"}


def test_pgvector检索_低于阈值不返回(store):
    """相似度阈值：不相关语料即使 top-k 有名额也不返回（与 fake 同一语义）。"""
    results = store.search("宇治桥 久美子 放学 路过 宇治川", k=3)

    assert "c3" not in {c.id for c in results}  # 轻音少女语料与查询无关


def test_pgvector检索_语义相近排前(store):
    results = store.search("大吉山 夜登山 吹奏", k=1)

    assert results[0].id == "c2"


def test_pgvector_upsert幂等(store):
    store.upsert(CORPUS)  # 再灌一遍
    store.upsert([CorpusChunk(id="c1", source="anitabi", work="吹响吧！上低音号",
                              text="宇治桥更新后的描述。")])

    results = store.search("宇治桥", k=1)

    assert results[0].id == "c1"
    assert "更新后" in results[0].text  # 重复灌库覆盖而非重复插入


# --- ingestion 解析（真实响应形状的 fixture） ---

BGM_SUBJECT_115908 = {
    "id": 115908,
    "name": "響け！ユーフォニアム",
    "name_cn": "吹响吧！上低音号",
    "summary": "进入北宇治高中就读的黄前久美子，在同班同学加藤叶月的热烈影响下，"
    "决定加入吹奏乐部。以全国大赛出场为目标的北宇治高中吹奏乐部的物语就此展开。",
}

def test_bangumi条目解析为chunk():
    chunks = chunks_from_bangumi_subject(BGM_SUBJECT_115908)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.id == "bangumi:115908"
    assert chunk.source == "bangumi.tv"
    assert "吹响吧！上低音号" in chunk.text
    assert "全国大赛" in chunk.text  # summary 进语料


def test_anitabi地标经Seichi解析为chunks():
    """anitabi 语料全程经 SeichiRepository 公开接口的 Seichi 结构（ADR-0001），
    元数据文本化（anitabi 无地标自由文本字段）。"""
    seichi = [
        Seichi(id="7gs3o1mm", name="宇治桥", work="吹响吧！上低音号", area="宇治市",
               lat=34.8929, lng=135.8065, ep=2),
        Seichi(id="qys7k4", name="大吉山展望台 蓝调", work="吹响吧！上低音号", area="宇治市",
               lat=34.8926, lng=135.8125, ep=8),
        Seichi(id="qys7j2", name="天ケ瀬ダム", work="吹响吧！上低音号", area="宇治市",
               lat=34.8808, lng=135.828, ep="OST"),
        Seichi(id="x0", name="无集数地标", work="吹响吧！上低音号", area="宇治市",
               lat=34.9, lng=135.8),
    ]

    chunks = chunks_from_seichi(seichi, subject_id=115908)

    assert len(chunks) == 4
    first = chunks[0]
    assert first.id == "anitabi:115908:7gs3o1mm"
    assert first.source == "anitabi"
    assert "宇治桥" in first.text
    assert "第2集" in first.text  # 出处集数进语料
    assert "大吉山展望台 蓝调" in chunks[1].text
    assert "OST" in chunks[2].text
    assert chunks[3].text.endswith("。") and "第" not in chunks[3].text  # 无集数不炸
