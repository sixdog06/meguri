"""JudgeProvider 端口与确定性实现（#10 LLM-as-judge）。

设计：judge(rubric, output) -> JudgeResult(score, reason)。rubric 是规则 id
（如 "narration_grounded"、"e2e:day_count_3"），RuleJudge 用确定性规则打分。
诚实边界：narration_grounded 只验证"讲解文本含站名、署名与站点 origin 一致"
——生成式讲解是否编造事实（超出元数据的场景描写）是真 LLM judge 的事
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
        if rubric == "narration_grounded":
            # 讲解接地：文本必须提到本站站名（模板/生成都得贴住本站）；
            # 来源署名必须等于站点的 origin（不许张冠李戴）
            name_ok = output["stop_name"] in output["text"]
            cite_ok = output["citation_source"] == output["origin"]
            passed = name_ok and cite_ok
            return JudgeResult(
                1.0 if passed else 0.0,
                "讲解含站名且署名与站点来源一致" if passed
                else f"接地失败（含站名={name_ok}，署名一致={cite_ok}）",
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
