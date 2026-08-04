"""第 1 步：Bangumi 全量动画灌库脚本的离线测试（fixture，不触网）。"""

import json
from datetime import date

import httpx

from app.ingest_bangumi import (
    BangumiCrawler,
    load_checkpoint,
    load_existing_ids,
    parse_subject,
    save_checkpoint,
    year_filter,
)


def test_parse_subject_正常与脏数据():
    # 真实 v0 search 响应形状：日期字段是 date（不是 air_date）
    item = {
        "id": 115908,
        "name": "響け！ユーフォニアム",
        "name_cn": "吹响吧！上低音号",
        "date": "2015-04-07",
        "summary": "吹奏乐部的故事",
        "extra_field": "忽略",
    }
    assert parse_subject(item) == {
        "id": 115908,
        "name": "響け！ユーフォニアム",
        "name_cn": "吹响吧！上低音号",
        "air_date": "2015-04-07",
        "summary": "吹奏乐部的故事",
    }
    # 兼容旧 air_date 字段（date 优先）
    assert parse_subject({"id": 1, "name": "X", "date": "2020-01-01", "air_date": "2019-01-01"})["air_date"] == "2020-01-01"
    assert parse_subject({"id": 1, "name": "X", "air_date": "2019-01-01"})["air_date"] == "2019-01-01"
    assert parse_subject({"name": "无 id"}) is None
    assert parse_subject({"id": 1, "name": "  "}) is None
    # name_cn/summary 缺失容忍为空串
    parsed = parse_subject({"id": 2, "name": "X"})
    assert parsed["name_cn"] == "" and parsed["summary"] == "" and parsed["air_date"] == ""


def test_year_filter():
    assert year_filter(2015) == {
        "type": [2],
        "air_date": [">=2015-01-01", "<=2015-12-31"],
    }


def test_checkpoint_断点续传语义(tmp_path):
    path = tmp_path / "ck.json"
    assert load_checkpoint(path) == set()  # 无文件从头开始
    save_checkpoint(path, {2015, 2016})
    assert load_checkpoint(path) == {2015, 2016}
    path.write_text("坏 JSON")
    assert load_checkpoint(path) == set()  # 坏文件从头开始


def test_load_existing_ids_幂等去重(tmp_path):
    path = tmp_path / "works.json"
    assert load_existing_ids(path) == set()
    path.write_text(json.dumps([{"id": 1}, {"id": 2}]))
    assert load_existing_ids(path) == {1, 2}


def _page(total: int, items: list[dict]) -> dict:
    return {"total": total, "data": items}


def test_fetch_range_大区间自动对半拆分(monkeypatch):
    """offset 被部署忽略时靠日期拆分拿全：total>页上限的区间被拆成小叶查询。"""
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        air = body["filter"]["air_date"]
        calls.append(f"{air[0]}~{air[1]}")
        # 整年 total=30（>10）；两个半年叶查询各 total=8 / 6
        if air[1] == "<=2015-12-31" and air[0] == ">=2015-01-01":
            return httpx.Response(200, json=_page(30, [{"id": i, "name": f"w{i}"} for i in range(10)]))
        if air[0] == ">=2015-01-01":  # 左半叶
            return httpx.Response(200, json=_page(8, [{"id": i, "name": f"w{i}"} for i in range(8)]))
        return httpx.Response(200, json=_page(6, [{"id": i + 8, "name": f"w{i}"} for i in range(6)]))

    import time

    monkeypatch.setattr(time, "sleep", lambda s: None)
    crawler = BangumiCrawler(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    results = crawler.fetch_range(date(2015, 1, 1), date(2015, 12, 31))

    assert len(calls) == 3  # 整年一次 + 两个半年叶查询
    assert {w["id"] for w in results} == set(range(14))  # 两片合并去重后全量


def test_fetch_year_按id去重(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        # 所有查询都返回同一页（offset 被忽略的部署行为）
        return httpx.Response(200, json=_page(10, [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]))

    import time

    monkeypatch.setattr(time, "sleep", lambda s: None)  # 跳过真实限速
    crawler = BangumiCrawler(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    results = crawler.fetch_year(2015)

    assert len(results) == 2  # 重复页不重复计入
