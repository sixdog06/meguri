"""anitabi 详细数据灌库脚本（第 2 步）：ID 库 → anitabi → 本地圣地数据包。

读 data/works/anime-1990plus.json 的作品 ID，逐作品经 AnitabiClient 拉
/bangumi/{id}/lite 与 /points/detail?haveImage=true，有巡礼数据的作品落成
data/seichi/{id}.json（沿用现有数据包格式，与 FileSeichiRepository 对齐）。

工程要点：幂等续传（已有文件跳过、404/空记录进进度文件不重试）、--force
重拉、限速+重试（与 ingest_bangumi 同一共享礼貌层）。本机 IP 对
api.anitabi.cn 间歇性 403——能拉多少拉多少。

用法：
  .venv/bin/python -m app.ingest_seichi              # 全量续传
  .venv/bin/python -m app.ingest_seichi --only 115908  # 只拉某作品
  .venv/bin/python -m app.ingest_seichi --force       # 无视已有文件重拉
"""

import argparse
import json
from pathlib import Path
from typing import Any

import httpx

from app.adapters.anitabi import AnitabiClient
from app.http_client import polite_call

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKS = _REPO_ROOT / "data/works/anime-1990plus.json"
DEFAULT_OUT_DIR = _REPO_ROOT / "data/seichi"
PROGRESS_FILE = ".ingest_progress.json"


# --- 纯函数（离线可测） ---


def point_to_pack(point: dict[str, Any], city: str) -> dict[str, Any] | None:
    """anitabi points/detail 单点 → 数据包格式（Seichi 字段对齐）；无坐标丢弃。"""
    geo = point.get("geo")
    if not geo:
        return None
    return {
        "id": point.get("id"),
        "name": point.get("cn") or point.get("name") or "",
        "lat": geo[0],
        "lng": geo[1],
        "image": point.get("image"),
        "ep": point.get("ep"),
        "ep_seconds": point.get("s"),
        "origin": point.get("origin"),
        "origin_url": point.get("originURL"),
        "area": city,
    }


def build_pack(subject_id: int, lite: dict[str, Any], points: list[dict[str, Any]]) -> dict[str, Any]:
    """lite + points → 完整数据包文档（无坐标点已过滤）。"""
    city = str(lite.get("city") or "")
    return {
        "subject_id": subject_id,
        "work": str(lite.get("cn") or lite.get("title") or ""),
        "city": city,
        "points": [p for p in (point_to_pack(pt, city) for pt in points) if p is not None],
    }


# --- 在线抓取 ---


class AnitabiCrawler:
    """anitabi 灌库抓取器：复用 AnitabiClient 的端点/解析，外套共享礼貌层
    （限速+重试；可注入 client 便于回放测试）。"""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._api = AnitabiClient(client=client)

    def fetch_pack(self, subject_id: int) -> dict[str, Any] | None:
        """拉某作品完整数据包；无巡礼数据（404/空点）返回 None，网络异常上抛。"""
        lite = polite_call(lambda: self._api.fetch_lite(subject_id))
        if lite is None:
            return None
        points = polite_call(lambda: self._api.fetch_points(subject_id))
        if not points:
            return None
        return build_pack(subject_id, lite, points)

    def localize_images(self, pack: dict[str, Any], images_dir: Path) -> int:
        """把数据包里的远程对照截图下载到本地，并就地改写 image 字段为
        本地 URL（/api/seichi-images/...，由后端静态挂载服务）。

        幂等（已有文件跳过）；单张失败保留远程 URL 不拖垮整包。返回本地化张数。
        """
        subject_dir = images_dir / str(pack["subject_id"])
        localized = 0
        for point in pack["points"]:
            url, point_id = point.get("image"), point.get("id")
            if not url or not point_id:
                continue
            dest = subject_dir / f"{point_id}.jpg"
            if not dest.exists():
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(polite_call(lambda: self._api.fetch_image(url)))
                except Exception as exc:
                    print(f"  截图下载失败，保留远程 URL（{point_id}: {type(exc).__name__}）")
                    continue
            point["image"] = f"/api/seichi-images/{pack['subject_id']}/{dest.name}"
            localized += 1
        return localized


def load_progress(path: Path) -> dict[str, Any]:
    """灌库进度（no_data 名单）；无文件视为无进度。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"no_data": []}


def main() -> None:
    parser = argparse.ArgumentParser(description="anitabi 详细数据灌库（幂等续传）")
    parser.add_argument("--works", type=Path, default=DEFAULT_WORKS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--only", type=int, default=None, help="只拉某 subjectID")
    parser.add_argument("--force", action="store_true", help="无视已有文件与进度重拉")
    parser.add_argument("--skip-images", action="store_true", help="不下载对照截图（image 保留远程 URL）")
    args = parser.parse_args()

    works = json.loads(args.works.read_text(encoding="utf-8"))
    ids = [args.only] if args.only else [w["id"] for w in works]
    progress_path = args.out_dir / PROGRESS_FILE
    progress = load_progress(progress_path) if not args.force else {"no_data": []}
    no_data = set(progress.get("no_data", []))
    crawler = AnitabiCrawler()

    done = skipped = failed = 0
    for subject_id in ids:
        target = args.out_dir / f"{subject_id}.json"
        if not args.force and (target.exists() or subject_id in no_data):
            skipped += 1
            continue
        try:
            pack = crawler.fetch_pack(subject_id)
        except Exception as exc:
            failed += 1
            print(f"{subject_id}: 抓取失败（{type(exc).__name__}: {exc}）")
            continue
        if pack is None:
            no_data.add(subject_id)
            skipped += 1
            continue
        if not args.skip_images:
            n = crawler.localize_images(pack, args.out_dir / "images")
            print(f"  截图本地化 {n}/{len(pack['points'])}")
        target.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
        done += 1
        print(f"{subject_id}: {pack['work']} {len(pack['points'])} 点")
        progress_path.write_text(
            json.dumps({"no_data": sorted(no_data)}), encoding="utf-8"
        )
    progress_path.write_text(json.dumps({"no_data": sorted(no_data)}), encoding="utf-8")
    print(f"完成：写入 {done}，跳过 {skipped}，失败 {failed}")


if __name__ == "__main__":
    main()
