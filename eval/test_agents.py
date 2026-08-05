"""评测 harness（#10）：golden 数据集离线回放 + 各 Agent 评分 + 报告。

与行为测试分离：不进 backend/pytest.ini 的 testpaths，需显式运行
（仓库根目录）：.venv/bin/python -m pytest eval/ -v -s
"""

import json

from eval.harness import (
    load_dataset,
    make_client,
    navigator_rules,
    plan_script,
    planner_rules,
    run_message,
    score_hit_rate,
    search_script,
)
from eval.judge import RuleJudge


def test_scout_检索命中率():
    cases = load_dataset("scout")
    scores = []
    for case in cases:
        client, _ = make_client(
            repo_seichi=case["repo_seichi"],
            llm_script=search_script(case["work"], case["area"]),
        )
        body = run_message(client, f"{case['area']}{case['work']}的圣地")
        got_ids = [s["id"] for s in body["seichi"]]
        score = score_hit_rate(case["expect_ids"], got_ids)
        scores.append(score)
        print(f"\n[eval:scout] {case['case']}: hit-rate={score:.2f} expect={case['expect_ids']} got={got_ids}")
    mean = sum(scores) / len(scores)
    print(f"\n[eval:scout] 平均命中率 = {mean:.2f}（{len(scores)} 例）")
    assert mean >= 0.5  # 报告导向的宽松下限


def test_planner_合理性规则():
    cases = load_dataset("planner")
    for case in cases:
        client, _ = make_client(
            repo_seichi=case["seichi"],
            llm_script=plan_script(case["work"], case["area"], case["days"]),
        )
        itinerary = run_message(client, "规划行程")["itinerary"]
        rules = planner_rules(itinerary, case["days"], len(case["seichi"]))
        score = sum(rules.values()) / len(rules)
        print(f"\n[eval:planner] {case['case']}: score={score:.2f} {json.dumps(rules, ensure_ascii=False)}")
        assert all(rules.values()), f"规则未全过: {rules}"


def test_navigator_时间可行性规则():
    cases = load_dataset("navigator")
    for case in cases:
        client, _ = make_client(
            repo_seichi=case["seichi"],
            llm_script=plan_script(case["work"], case["area"], case["days"]),
            transit_routes=case["transit"],
            hours=case["hours"],
        )
        itinerary = run_message(client, "规划行程")["itinerary"]
        rules = navigator_rules(itinerary, case["expect_closed_ids"])
        score = sum(rules.values()) / len(rules)
        print(f"\n[eval:navigator] {case['case']}: score={score:.2f} {json.dumps(rules, ensure_ascii=False)}")
        assert all(rules.values()), f"规则未全过: {rules}"


def test_storyteller_citation_fidelity_judge():
    judge = RuleJudge()
    cases = load_dataset("storyteller")
    for case in cases:
        client, _ = make_client(
            repo_seichi=case["seichi"],
            llm_script=plan_script(case["work"], case["area"], case["days"]),
            chunks=case["chunks"],
        )
        itinerary = run_message(client, "规划行程")["itinerary"]
        chunk_by_id = {c["id"]: c for c in case["chunks"]}
        narrations = [n for d in itinerary["days"] for n in d["narrations"]]
        assert narrations, "本案例应产出讲解"
        scores = []
        for n in narrations:
            chunk = chunk_by_id[n["citation"]["chunk_id"]]
            result = judge.judge(
                "citation_fidelity", {"narration": n["text"], "chunk_text": chunk["text"]}
            )
            scores.append(result.score)
            if not result.score:
                print(f"\n[eval:storyteller] 不保真: {n['seichi_id']} {result.reason}")
        mean = sum(scores) / len(scores)
        print(f"\n[eval:storyteller] {case['case']}: citation_fidelity={mean:.2f}（{len(scores)} 条讲解）")
        assert mean >= case["min_judge_score"]


def test_e2e_检查清单回放评分(tmp_path):
    """e2e 回放 + RuleJudge 清单评分；trace 经 JsonlTracer 落盘消费（#10）。"""
    from app.api.conversations import get_tracer
    from app.agents.tracing import JsonlTracer
    from app.main import app

    judge = RuleJudge()
    cases = load_dataset("e2e")
    for case in cases:
        client, _ = make_client(
            repo_seichi=case["seichi"],
            llm_script=plan_script(case["work"], case["area"], case["days"]),
            chunks=case.get("chunks", []),
        )
        # JsonlTracer 真实消费：trace 落临时文件，回放后读 JSONL 验证可读
        trace_path = tmp_path / "trace.jsonl"
        app.dependency_overrides[get_tracer] = lambda: JsonlTracer(str(trace_path))
        body = run_message(client, case["input"])
        itinerary = body["itinerary"]
        legs = [leg for d in itinerary["days"] for leg in d["legs"]]
        checks = [c for d in itinerary["days"] for c in d["checks"]]
        narrations = [n for d in itinerary["days"] for n in d["narrations"]]
        trace_events = [
            json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()
        ]
        trace_names = {e["name"] for e in trace_events}
        predicates = {
            "reply_non_empty": bool(body["reply"]),
            "day_count_3": itinerary["day_count"] == 3,
            "legs_present": len(legs) > 0,
            "checks_present": len(checks) > 0,
            "narration_with_citation": any(n["citation"] for n in narrations),
            "traced_pipeline": {"loop_step", "llm_call", "tool_call", "pipeline_stage"}
            <= trace_names,
        }
        results = {
            item: judge.judge(f"e2e:{item}", {"predicate": predicates[item]})
            for item in case["checklist"]
        }
        score = sum(r.score for r in results.values()) / len(results)
        print(f"\n[eval:e2e] {case['case']}: score={score:.2f}")
        for item, r in results.items():
            print(f"  {'✓' if r.score else '✗'} {item}: {r.reason}")
        assert score == 1.0, "检查清单未全过"
