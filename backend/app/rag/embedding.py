"""EmbeddingProvider 实现（#8）。

- HashEmbeddingProvider：确定性哈希向量（token 桶哈希 + L2 归一化）。
  无真实 embedding key 时的开发/测试 fake——共享 token 的文本余弦相近，
  足够驱动 pgvector 检索全链路。真实 embedding（OpenAI 兼容）随 LangChain
  适配层接入落地（ADR-0002），落地前一律用哈希向量。
"""

import hashlib
import math
import re

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
    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in tokenize(text):
            bucket = int(hashlib.md5(token.encode()).hexdigest(), 16) % self._dim
            vector[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]
