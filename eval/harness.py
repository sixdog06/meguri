"""评测 harness（#10）：golden 数据集加载 + HTTP 缝回放 + 评分。

离线回放：fake 适配器 + 数据集夹具驱动完整 HTTP 缝流程，对照期望评分，
命令行输出报告（不进 CI 门禁，见 eval/conftest.py 头部用法）。
客户端装配与行为测试共享 backend/testsupport。
"""

import json
import random
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from testsupport import make_client  # noqa: F401  (eval 测试经此使用)

from app.geo import haversine_km

DATASETS = Path(__file__).resolve().parent / "datasets"


def load_dataset(name: str) -> list[dict[str, Any]]:
    path = DATASETS / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def plan_script(work: str, area: str, days: int) -> list[dict[str, Any]]:
    args: dict[str, Any] = {"ani_name": work, "area": area, "days": days}
    return [
        {"type": "tool_call", "name": "plan_itinerary", "args": args},
        {"type": "final", "content": "评测回复"},
    ]


def search_script(work: str, area: str) -> list[dict[str, Any]]:
    return [
        {"type": "tool_call", "name": "search_seichi", "args": {"ani_name": work, "area": area}},
        {"type": "final", "content": "评测回复"},
    ]


def run_message(client: TestClient, text: str) -> dict[str, Any]:
    cid = client.post("/api/conversations").json()["conversation_id"]
    response = client.post(f"/api/conversations/{cid}/messages", json={"text": text})
    assert response.status_code == 200
    return response.json()


# --- 评分 ---


def score_hit_rate(expect_ids: list[str], got_ids: list[str]) -> float:
    """Scout 命中率：期望命中的 id 子集被检索返回的比例。"""
    if not expect_ids:
        return 1.0 if not got_ids else 0.0
    return len(set(expect_ids) & set(got_ids)) / len(expect_ids)


def planner_rules(itinerary: dict[str, Any], expect_days: int, candidate_count: int) -> dict[str, bool]:
    """Planner 合理性规则（确定性，非主观断言）。"""
    days = itinerary["days"]
    stops = [s for d in days for s in d["seichi"]]
    # 最近邻排序不劣于随机基线：逐天比较 NN 距离与 20 次固定种子 shuffle 的均值
    rng = random.Random(42)
    nn_total = 0.0
    baseline_total = 0.0
    for d in days:
        coords = {s["id"]: (s["lat"], s["lng"]) for s in d["seichi"]}
        nn_total += sum(
            leg["distance_km"] for leg in d["legs"] if not leg["cross_day"]
        )
        ids = [s["id"] for s in d["seichi"]]
        for _ in range(20):
            shuffled = ids[:]
            rng.shuffle(shuffled)
            baseline_total += (
                sum(
                    haversine_km(*coords[a], *coords[b])
                    for a, b in zip(shuffled, shuffled[1:])
                )
                / 20
            )
    return {
        "day_count_correct": itinerary["day_count"] == expect_days,
        "every_day_non_empty": all(len(d["seichi"]) >= 1 for d in days),
        "full_coverage": len(stops) == candidate_count,
        "nn_beats_random_baseline": nn_total <= baseline_total,
    }


def navigator_rules(itinerary: dict[str, Any]) -> dict[str, bool]:
    """Navigator 时间可行性规则。"""
    all_checks = [c for d in itinerary["days"] for c in d["checks"]]
    stops = [s for d in itinerary["days"] for s in d["seichi"]]

    def monotonic(day: dict[str, Any]) -> bool:
        times = [c["arrive_time"] for c in day["checks"]]
        return times == sorted(times)

    return {
        "checks_for_every_stop": len(all_checks) == len(stops),
        "arrive_time_format": all(
            len(c["arrive_time"]) == 5 and c["arrive_time"][2] == ":" for c in all_checks
        ),
        "times_monotonic_within_day": all(monotonic(d) for d in itinerary["days"]),
    }
