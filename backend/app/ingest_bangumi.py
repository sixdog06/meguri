"""Bangumi 全量动画灌库脚本（用户拍板的数据层架构：Bangumi = 作品 ID 空间）。

用 Bangumi v0 API（https://bangumi.github.io/api/）拉 type=2（动画）且
air_date >= 1990-01-01 的全部作品，存成本地 JSON（data/works/anime-1990plus.json），
字段 {id, name, name_cn, air_date, summary}——summary 保留在源 JSON 里备用
（当前 DB 只灌名字字段，见 app.ingest_works）。

工程要点：
- 自定义 User-Agent（bgm.tv API 强制要求），限速每秒 ≤2 请求防 ban；
- 按年遍历 + checkpoint 断点续传：幂等，重跑跳过已完成年份（本机对
  api.bgm.tv 抖动严重，靠多次运行推进）；
- 单请求失败重试 3 次仍失败则该年不记 checkpoint，下次运行重试该年。

用法：.venv/bin/python -m app.ingest_bangumi [--start-year 1990] [--end-year 当年]
"""

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.http_client import USER_AGENT, polite_call

SEARCH_URL = "https://api.bgm.tv/v0/search/subjects"
# 该 API 部署实际每页上限 10（请求更大 limit 也只回 10），按响应里的 total 翻页
PAGE_SIZE = 10

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = _REPO_ROOT / "data/works/anime-1990plus.json"
CHECKPOINT_SUFFIX = ".checkpoint.json"


# --- 纯函数（离线可测） ---


def parse_subject(item: dict[str, Any]) -> dict[str, Any] | None:
    """单条 search 结果 → 存储格式；缺 id/name 的脏数据丢弃。

    日期字段：v0 search 实际返回 `date`（早期误读 `air_date` 导致全空，
    兼容两字段以 `date` 优先）。
    """
    subject_id = item.get("id")
    name = (item.get("name") or "").strip()
    if subject_id is None or not name:  # id=0 是合法值，不能用 falsy 判断
        return None
    return {
        "id": subject_id,
        "name": name,
        "name_cn": (item.get("name_cn") or "").strip(),
        "air_date": item.get("date") or item.get("air_date") or "",
        "summary": (item.get("summary") or "").strip(),
    }


def year_filter(year: int) -> dict[str, Any]:
    """某年的 search filter（type=2 动画 + air_date 全年）。"""
    return {"type": [2], "air_date": [f">={year}-01-01", f"<={year}-12-31"]}


def load_checkpoint(path: Path) -> set[int]:
    """已完成年份集合；无文件/坏 JSON 视为无进度（从头开始）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(data.get("completed_years", []))


def save_checkpoint(path: Path, completed: set[int]) -> None:
    path.write_text(
        json.dumps({"completed_years": sorted(completed)}, ensure_ascii=False),
        encoding="utf-8",
    )


def load_existing_ids(path: Path) -> set[int]:
    """已有作品 id 集合（幂等去重；无文件视为空）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {item["id"] for item in data}


# --- 在线抓取 ---

# 该 API 部署（meilisearch 后端）对 filter-only 查询忽略 offset——深分页永远
# 返回首页。稳妥替代：按日期区间递归对半拆分，直到每片 total ≤ 页上限。
_MAX_SPLIT_DEPTH = 24


class BangumiCrawler:
    """限速 + 重试的 Bangumi 抓取器（可注入 client 便于回放测试）。"""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=20, headers={"User-Agent": USER_AGENT}
        )

    def _search_page(self, start: date, end: date) -> dict[str, Any]:
        """单页查询（经共享礼貌层限速+重试；重试耗尽上抛）。"""
        payload = {
            "keyword": "",
            "sort": "rank",
            "filter": {"type": [2], "air_date": [f">={start}", f"<={end}"]},
            "limit": PAGE_SIZE,
            "offset": 0,
        }
        return polite_call(
            lambda: self._client.post(SEARCH_URL, json=payload).raise_for_status().json()
        )

    def fetch_range(self, start: date, end: date, depth: int = 0) -> list[dict[str, Any]]:
        """抓 [start, end] 区间全部作品（区间过大自动对半拆分）。"""
        data = self._search_page(start, end)
        total = data.get("total") or 0
        items = data.get("data") or []
        if total <= PAGE_SIZE or start >= end or depth >= _MAX_SPLIT_DEPTH:
            if total > PAGE_SIZE:
                print(f"  警告：{start}~{end} total={total} 超过页上限且不可再分，仅取首页")
            return [p for p in (parse_subject(i) for i in items) if p is not None]
        mid = start + (end - start) // 2  # timedelta 整除得区间中点
        return self.fetch_range(start, mid, depth + 1) + self.fetch_range(
            mid + timedelta(days=1), end, depth + 1
        )

    def fetch_year(self, year: int) -> list[dict[str, Any]]:
        """抓某年全部作品（经日期拆分；按 id 去重防重叠页）。"""
        results = self.fetch_range(date(year, 1, 1), date(year, 12, 31))
        by_id = {w["id"]: w for w in results}
        return list(by_id.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Bangumi 全量动画灌库（断点续传）")
    parser.add_argument("--start-year", type=int, default=1990)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = out.with_suffix(CHECKPOINT_SUFFIX)
    completed = load_checkpoint(checkpoint_path)
    known_ids = load_existing_ids(out)
    crawler = BangumiCrawler()

    for year in range(args.start_year, args.end_year + 1):
        if year in completed:
            print(f"skip {year}（已完成）")
            continue
        try:
            works = crawler.fetch_year(year)
        except Exception as exc:
            print(f"{year}: 抓取失败（{type(exc).__name__}: {exc}），下轮重试")
            continue
        # 幂等合并：去重后重写全量文件
        new = [w for w in works if w["id"] not in known_ids]
        known_ids.update(w["id"] for w in works)
        existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
        out.write_text(
            json.dumps(existing + new, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        completed.add(year)
        save_checkpoint(checkpoint_path, completed)
        print(f"{year}: +{len(new)}（累计 {len(known_ids)}）")
    print(f"完成：{len(known_ids)} 条 → {out}")


if __name__ == "__main__":
    main()
