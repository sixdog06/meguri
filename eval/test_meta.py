"""评测 harness 的元测试（#10）：评分逻辑自身的正确性。"""

from eval.harness import navigator_rules, planner_rules, score_hit_rate
from eval.judge import RuleJudge


def test_hit_rate_计算():
    assert score_hit_rate(["a", "b"], ["a", "b", "c"]) == 1.0
    assert score_hit_rate(["a", "b"], ["a"]) == 0.5
    assert score_hit_rate([], []) == 1.0  # 期望空且结果空 = 满分
    assert score_hit_rate([], ["x"]) == 0.0  # 期望空但返回了 = 零分


def test_rule_judge_citation_fidelity():
    judge = RuleJudge()
    ok = judge.judge(
        "citation_fidelity", {"narration": "宇治桥是名场面…", "chunk_text": "宇治桥是名场面之一。"}
    )
    assert ok.score == 1.0 and ok.reason
    bad = judge.judge(
        "citation_fidelity", {"narration": "编造的内容", "chunk_text": "宇治桥是名场面之一。"}
    )
    assert bad.score == 0.0


def test_rule_judge_e2e():
    judge = RuleJudge()
    assert judge.judge("e2e:x", {"predicate": True}).score == 1.0
    assert judge.judge("e2e:x", {"predicate": False}).score == 0.0


def test_planner_rules_能识别坏行程():
    bad_itinerary = {
        "day_count": 2,  # 期望 3
        "days": [
            {"day": 1, "seichi": [{"id": "a", "lat": 34.9, "lng": 135.8}],
             "legs": []},
            {"day": 2, "seichi": [], "legs": []},  # 空天
        ],
    }
    rules = planner_rules(bad_itinerary, expect_days=3, candidate_count=2)
    assert rules["day_count_correct"] is False
    assert rules["every_day_non_empty"] is False
    assert rules["full_coverage"] is False


def test_navigator_rules_能识别坏校验():
    bad_itinerary = {
        "day_count": 1,
        "days": [
            {
                "day": 1,
                "seichi": [{"id": "a"}, {"id": "b"}],
                "legs": [],
                "checks": [{"seichi_id": "a", "arrive_time": "10:00"},
                           {"seichi_id": "b", "arrive_time": "09:00"}],
            }
        ],
    }
    rules = navigator_rules(bad_itinerary)
    assert rules["times_monotonic_within_day"] is False
    assert rules["checks_for_every_stop"] is True
