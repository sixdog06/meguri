"""EmbeddingProvider 实现（#8）。

- HashEmbeddingProvider：确定性哈希向量（token 桶哈希 + L2 归一化）。
  无真实 embedding key 时的开发/测试 fake——共享 token 的文本余弦相近，
  足够驱动 pgvector 检索全链路。
- OpenAIEmbeddingProvider：OpenAI 兼容 embeddings 接口的 live 实现
  （ADR-0002），配 MEGURI_OPENAI_API_KEY 后由 wiring 自动选用。
"""

import hashlib
import math
import re

from openai import OpenAI, OpenAIError

EMBEDDING_DIM = 64  # 默认值；实际维度以 settings.embedding_dim 为准（改维度需重建 corpus 表）

# 检索相似度阈值（余弦相似度下限）：哈希向量下的保守估计——实测相关文本
# ≈0.79、同作品泛条目 ≈0.53-0.59、无关文本 ≈0.22，取 0.6 把"同作品但与本站
# 无关"的语料也挡在门外（citation 不给错配背书）。接真实 embedding 后必须
# 重新标定（真向量分布不同，通常可降到 0.3 左右）。
DEFAULT_MIN_SCORE = 0.6

_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9]+|[一-鿿]")


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度（向量已归一化时即点积；这里做完整计算不假设归一化）。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def tokenize(text: str) -> list[str]:
    """中英混合简易分词：英文数字按词，CJK 按字 bigram。"""
    tokens: list[str] = []
    for match in _TOKEN_PATTERN.finditer(text.lower()):
        token = match.group(0)
        if len(token) == 1 and "一" <= token <= "鿿":
            tokens.append(token)  # 单字保留，bigram 在调用方组合
        else:
            tokens.append(token)
    # CJK 单字序列转 bigram（提升中文重叠度信号）
    bigrams: list[str] = []
    run: list[str] = []
    for token in tokens:
        if len(token) == 1 and "一" <= token <= "鿿":
            run.append(token)
        else:
            run = []
        if len(run) >= 2:
            bigrams.append(run[-2] + run[-1])
    return tokens + bigrams


class HashEmbeddingProvider:
    """确定性哈希向量：同一文本恒得同向量，共享 token 的文本余弦相近。"""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """EmbeddingProvider 契约：逐文本返回 dim 维向量（确定性）。"""
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        """token 桶哈希计数 + L2 归一化。"""
        vector = [0.0] * self._dim
        for token in tokenize(text):
            bucket = int(hashlib.md5(token.encode()).hexdigest(), 16) % self._dim
            vector[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class EmbeddingUnavailableError(Exception):
    """embedding 服务调用失败（网络/4xx）——明确上抛，不静默降级哈希向量。"""


class EmbeddingDimensionError(Exception):
    """API 返回维度与 MEGURI_EMBEDDING_DIM 不符——需调整配置并重建语料。"""


class OpenAIEmbeddingProvider:
    """OpenAI 兼容 embeddings 接口的 live 实现（经 openai SDK，同 LLM 网关）。

    请求按 dim 传 dimensions 参数（text-embedding-3 系支持服务端降维），
    让真向量直接对齐 corpus_chunks 的 Vector 列。维度是硬约束：API 不支持
    dimensions（4xx 报错上抛 EmbeddingUnavailableError）或忽略该参数返回
    全维度（抛 EmbeddingDimensionError），都不静默截断——截断会毁掉向量
    空间语义，宁可明确失败。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dim: int = EMBEDDING_DIM,
        *,
        client: OpenAI | None = None,  # 测试注入假客户端，不触网
        timeout: float = 30.0,
    ) -> None:
        self._model = model
        self._dim = dim
        self._client = client or OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """EmbeddingProvider 契约：逐文本返回 dim 维向量（顺序与输入一致）。"""
        if not texts:
            return []
        try:
            response = self._client.embeddings.create(
                model=self._model, input=texts, dimensions=self._dim
            )
        except OpenAIError as exc:
            raise EmbeddingUnavailableError(
                f"embedding 请求失败（model={self._model}）：{exc}"
            ) from exc
        # 按 index 归位（响应顺序不保证与输入一致）
        vectors: list[list[float] | None] = [None] * len(texts)
        for item in response.data:
            vectors[item.index] = [float(v) for v in item.embedding]
        result: list[list[float]] = []
        for vector in vectors:
            if vector is None:
                raise EmbeddingUnavailableError("embedding 响应条目数与输入不符")
            if len(vector) != self._dim:
                raise EmbeddingDimensionError(
                    f"embedding 返回维度 {len(vector)} 与 MEGURI_EMBEDDING_DIM={self._dim} "
                    f"不符（model={self._model} 可能不支持 dimensions 参数）：请把 "
                    "MEGURI_EMBEDDING_DIM 调整为该模型维度并重建语料"
                    "（DROP TABLE corpus_chunks + 重新灌库）"
                )
            result.append(vector)
        return result
