"""评测 harness（#10）：golden 数据集离线回放 + 各 Agent 评分 + 报告。

与行为测试分离：不进 backend/pytest.ini 的 testpaths，需显式运行
（仓库根目录）：.venv/bin/python -m pytest eval/ -v -s
"""

import json

import pytest

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
        )
        itinerary = run_message(client, "规划行程")["itinerary"]
        rules = navigator_rules(itinerary)
        score = sum(rules.values()) / len(rules)
        print(f"\n[eval:navigator] {case['case']}: score={score:.2f} {json.dumps(rules, ensure_ascii=False)}")
        assert all(rules.values()), f"规则未全过: {rules}"


def test_storyteller_讲解接地_judge():
    judge = RuleJudge()
    cases = load_dataset("storyteller")
    for case in cases:
        client, _ = make_client(
            repo_seichi=case["seichi"],
            llm_script=plan_script(case["work"], case["area"], case["days"]),
        )
        itinerary = run_message(client, "规划行程")["itinerary"]
        stop_by_id = {s["id"]: s for s in case["seichi"]}
        narrations = [n for d in itinerary["days"] for n in d["narrations"]]
        assert narrations, "本案例应产出讲解"
        scores = []
        for n in narrations:
            stop = stop_by_id[n["seichi_id"]]
            citation = n["citation"] or {}
            result = judge.judge(
                "narration_grounded",
                {
                    "text": n["text"],
                    "stop_name": stop["name"],
                    "citation_source": citation.get("source"),
                    "origin": stop.get("origin"),
                },
            )
            scores.append(result.score)
            if not result.score:
                print(f"\n[eval:storyteller] 不接地: {n['seichi_id']} {result.reason}")
        mean = sum(scores) / len(scores)
        print(f"\n[eval:storyteller] {case['case']}: narration_grounded={mean:.2f}（{len(scores)} 条讲解）")
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


# --- 一期：作品名解析（anime_works 表 + pg_trgm） ---


@pytest.fixture(scope="module")
def works_resolver():
    """真实全量索引（19144 条）灌进测试库 anime_works 表后的 DB 解析器。"""
    from app.adapters.works_db import DbWorksResolver
    from app.db import _get_engine
    from app.ingest_works import load_works

    load_works()
    return DbWorksResolver(_get_engine())


def test_work_resolve_解析准确率(works_resolver):
    """作品名 → subjectID：子串快速路径 + trigram 模糊兜底，逐 case 断言。"""
    cases = load_dataset("work_resolve")
    passed = 0
    for case in cases:
        refs = works_resolver.resolve_works(case["query"])
        got_ids = [r.subject_id for r in refs]
        ok = True
        if "expect_top1" in case:
            ok = bool(got_ids) and got_ids[0] == case["expect_top1"]
        if "expect_ids" in case:
            ok = ok and set(case["expect_ids"]) <= set(got_ids)
        passed += ok
        print(f"\n[eval:resolve] {case['case']}: {'✓' if ok else '✗'} query={case['query']!r} got={got_ids[:5]}")
    print(f"\n[eval:resolve] 通过率 = {passed}/{len(cases)}")
    assert passed == len(cases), "作品名解析有 case 未达标（阈值需标定）"
