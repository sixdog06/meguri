"""作品名解析 DB 版（anime_works 表 + pg_trgm）：子串快速路径 + trigram 模糊兜底。

真实 Postgres（5433 meguri_test，testsupport 已建表 + pg_trgm 扩展）。
"""

import pytest
from sqlalchemy import text

from app.adapters.works_db import DbWorksResolver
from app.db import _get_engine

FIXTURE_WORKS = [
    (1424, "けいおん！", "轻音少女"),
    (3774, "けいおん！！", "轻音少女 第二季"),
    (12426, "映画けいおん！", "轻音少女 剧场版"),
    (115908, "響け！ユーフォニアム", "吹响吧！上低音号"),
    (32281, "君の名は。", "你的名字。"),
    (2875, "死神剧场版 消逝于黑暗中 呼唤着你的名字", ""),
]


@pytest.fixture(scope="module")
def resolver():
    engine = _get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM anime_works"))
        for subject_id, name, name_cn in FIXTURE_WORKS:
            conn.execute(
                text(
                    "INSERT INTO anime_works (subject_id, name, name_cn, name_norm,"
                    " name_cn_norm, air_date)"
                    " VALUES (:id, :name, :name_cn, :nn, :ncn, '')"
                ),
                {
                    "id": subject_id,
                    "name": name,
                    "name_cn": name_cn,
                    "nn": "".join(name.split()),
                    "ncn": "".join(name_cn.split()),
                },
            )
        conn.commit()
    return DbWorksResolver(engine)


def test_子串匹配_多作品全命中短名在前(resolver):
    refs = resolver.resolve_works("轻音少女")

    assert [r.subject_id for r in refs] == [1424, 3774, 12426]
    assert refs[0].name == "轻音少女"


def test_子串匹配_空白忽略(resolver):
    """查询无空白、库里带空白："轻音少女第二季" 命中 "轻音少女 第二季"。"""
    refs = resolver.resolve_works("轻音少女第二季")

    assert refs[0].subject_id == 3774


def test_子串匹配_日文原名(resolver):
    refs = resolver.resolve_works("ユーフォニアム")

    assert refs[0].subject_id == 115908


def test_子串匹配_取最短名(resolver):
    """"你的名字" 命中《你的名字。》而非名字更长的《…呼唤着你的名字》。"""
    refs = resolver.resolve_works("你的名字")

    assert refs[0].subject_id == 32281


def test_模糊匹配_错字救回(resolver):
    """子串无命中（少了个标点）时 trigram 相似度兜底。"""
    refs = resolver.resolve_works("吹响吧上低音号")

    assert refs and refs[0].subject_id == 115908


def test_俗名无字面重合仍不命中(resolver):
    """"京吹" 与官方名无公共 trigram——俗名归一化是 LLM 的职责，不是检索层。"""
    assert resolver.resolve_works("京吹") == []


def test_未收录返回空(resolver):
    assert resolver.resolve_works("完全不存在的作品xyz") == []


def test_空输入返回空(resolver):
    assert resolver.resolve_works("") == []
    assert resolver.resolve_works("   ") == []
