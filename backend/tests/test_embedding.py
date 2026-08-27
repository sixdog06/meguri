"""OpenAIEmbeddingProvider：HTTP 层用注入的假客户端测，不触网。

维度是硬约束：dimensions 参数对齐 MEGURI_EMBEDDING_DIM；API 报错或返回
维度不符都明确上抛，不静默截断/降级。
"""

from types import SimpleNamespace

import pytest
from openai import OpenAIError

from app.rag.embedding import (
    EmbeddingDimensionError,
    EmbeddingUnavailableError,
    OpenAIEmbeddingProvider,
)


class _FakeEmbeddingsAPI:
    """假 embeddings 接口：记录请求参数，返回罐头响应或抛错。"""

    def __init__(self, vectors: list[list[float]] | None = None,
                 error: Exception | None = None) -> None:
        self._vectors = vectors or []
        self._error = error
        self.calls: list[dict] = []

    def create(self, *, model, input, dimensions):
        self.calls.append({"model": model, "input": input, "dimensions": dimensions})
        if self._error:
            raise self._error
        # 故意乱序返回，验证按 index 归位
        data = [
            SimpleNamespace(index=i, embedding=v)
            for i, v in reversed(list(enumerate(self._vectors)))
        ]
        return SimpleNamespace(data=data)


def _provider(api: _FakeEmbeddingsAPI, dim: int = 3) -> OpenAIEmbeddingProvider:
    return OpenAIEmbeddingProvider(
        base_url="http://fake/v1",
        api_key="test-key",
        model="text-embedding-3-small",
        dim=dim,
        client=SimpleNamespace(embeddings=api),
    )


def test_embed_传dimensions参数并按index归位():
    api = _FakeEmbeddingsAPI(vectors=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    provider = _provider(api, dim=3)

    vectors = provider.embed(["甲", "乙"])

    assert api.calls == [{
        "model": "text-embedding-3-small",
        "input": ["甲", "乙"],
        "dimensions": 3,
    }]
    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]  # 乱序响应已归位


def test_embed_空输入不调API():
    api = _FakeEmbeddingsAPI()

    assert _provider(api).embed([]) == []
    assert api.calls == []


def test_embed_API报错上抛EmbeddingUnavailableError():
    api = _FakeEmbeddingsAPI(error=OpenAIError("connection refused"))

    with pytest.raises(EmbeddingUnavailableError, match="connection refused"):
        _provider(api).embed(["甲"])


def test_embed_返回维度不符抛EmbeddingDimensionError():
    """API 忽略 dimensions 返回全维度（如 1536）：明确报错提示调整配置重建语料。"""
    api = _FakeEmbeddingsAPI(vectors=[[0.1] * 1536])

    with pytest.raises(EmbeddingDimensionError, match="MEGURI_EMBEDDING_DIM"):
        _provider(api, dim=64).embed(["甲"])


def test_embed_响应条目缺失抛EmbeddingUnavailableError():
    api = _FakeEmbeddingsAPI(vectors=[[0.1, 0.2, 0.3]])  # 请求 2 条只回 1 条

    with pytest.raises(EmbeddingUnavailableError, match="条目数"):
        _provider(api).embed(["甲", "乙"])
