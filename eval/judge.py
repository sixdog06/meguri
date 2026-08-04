"""JudgeProvider 端口与确定性实现（#10 LLM-as-judge）。

设计：judge(rubric, output) -> JudgeResult(score, reason)。rubric 是规则 id
（如 "citation_fidelity"、"e2e:day_count_3"），RuleJudge 用确定性规则打分。
诚实边界：citation_fidelity 只验证"讲解文本确实是其 citation chunk 的摘录
且该 chunk 是该圣地的实际检索结果"——对检索式拼装实现这近乎恒真；真正的
生成式事实性判断（LLM 生成的讲解是否忠于语料）是真 LLM judge 的事
（OpenAIJudge，当前 stub，配置位 config.openai_*），见 README 评测一节。
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class JudgeResult:
    score: float  # 0.0 - 1.0
    reason: str


class JudgeProvider(Protocol):
    def judge(self, rubric: str, output: dict[str, Any]) -> JudgeResult: ...


class RuleJudge:
    """确定性规则 judge（fake）：rubric id → 规则断言。接真 LLM 后替换。"""

    def judge(self, rubric: str, output: dict[str, Any]) -> JudgeResult:
        if rubric == "citation_fidelity":
            # 引用保真：讲解文本必须是其 citation 语料原文的摘录
            narration = output["narration"]
            chunk_text = output["chunk_text"]
            supported = narration.rstrip("…") in chunk_text
            return JudgeResult(
                1.0 if supported else 0.0,
                "讲解是其 citation chunk 的摘录" if supported else "讲解不在 citation 语料原文中",
            )
        if rubric.startswith("e2e:"):
            passed = bool(output["predicate"])
            return JudgeResult(
                1.0 if passed else 0.0,
                f"{'通过' if passed else '未通过'}：{rubric[4:]}",
            )
        raise ValueError(f"未知 rubric：{rubric}")


class OpenAIJudge:
    """真 LLM judge —— stub（配置位 config.openai_*；随真实 key 接入落地）。

    生成式事实性（讲解是否忠于语料）是它的职责，RuleJudge 不冒充。"""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url, self._api_key, self._model = base_url, api_key, model

    def judge(self, rubric: str, output: dict[str, Any]) -> JudgeResult:
        raise NotImplementedError("真 LLM judge 随真实 key 接入落地；离线评测用 RuleJudge")
