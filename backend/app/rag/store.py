"""CorpusStore 实现（#8）：内存 fake + pgvector live——同一检索语义。

两者都用注入的 EmbeddingProvider 向量化、余弦相似度排序 top-k，并应用同一
相似度阈值（min_score）：不达标 = 无命中（citation 不给错配背书）。
fake 与 live 只是存储介质不同（内存 dict vs pgvector 表），语义不分叉。
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.adapters.ports import CorpusChunk, EmbeddingProvider
from app.models import CorpusChunkRow
from app.rag.embedding import DEFAULT_MIN_SCORE, HashEmbeddingProvider, cosine_similarity


class InMemoryCorpusStore:
    """内存版 fake：与 live 相同的哈希向量 + 余弦 + 阈值语义。"""

    def __init__(
        self,
        chunks: list[CorpusChunk] | None = None,
        *,
        embedder: EmbeddingProvider | None = None,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> None:
        self._embedder = embedder or HashEmbeddingProvider()
        self._min_score = min_score
        self._chunks: dict[str, CorpusChunk] = {}
        self._vectors: dict[str, list[float]] = {}
        if chunks:
            self.upsert(chunks)

    def upsert(self, chunks: list[CorpusChunk]) -> None:
        """写入/覆盖语料块（按 id 幂等），并重算其向量。"""
        vectors = self._embedder.embed([c.text for c in chunks]) if chunks else []
        for chunk, vector in zip(chunks, vectors):
            self._chunks[chunk.id] = chunk
            self._vectors[chunk.id] = vector

    def search(self, query: str, k: int) -> list[CorpusChunk]:
        """余弦相似度 top-k 检索；低于 min_score 阈值的一律不返回。"""
        query_vector = self._embedder.embed([query])[0]
        scored = [
            (cosine_similarity(query_vector, self._vectors[cid]), self._chunks[cid])
            for cid in self._chunks
        ]
        scored = [item for item in scored if item[0] >= self._min_score]
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [c for _, c in scored[:k]]


class PgVectorCorpusStore:
    """pgvector 版 live 实现：向量存 corpus_chunks 表，SQL 层做距离过滤与排序。"""

    def __init__(
        self,
        engine: Engine,
        embedder: EmbeddingProvider,
        *,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> None:
        self._engine = engine
        self._embedder = embedder
        self._max_distance = 1.0 - min_score  # 余弦距离 = 1 - 余弦相似度

    def upsert(self, chunks: list[CorpusChunk]) -> None:
        """写入/覆盖语料块（merge 按主键幂等），向量随文本重算。"""
        if not chunks:
            return
        vectors = self._embedder.embed([c.text for c in chunks])
        with Session(self._engine) as session:
            for chunk, vector in zip(chunks, vectors):
                session.merge(
                    CorpusChunkRow(
                        id=chunk.id,
                        source=chunk.source,
                        work=chunk.work,
                        text=chunk.text,
                        embedding=vector,
                    )
                )
            session.commit()

    def search(self, query: str, k: int) -> list[CorpusChunk]:
        """余弦距离 <=> 排序 top-k；超过 max_distance（= 1 - 阈值）不返回。"""
        vector = self._embedder.embed([query])[0]
        # psycopg 会把 list[float] 绑成 float8[]（没有到 vector 的 cast），
        # 传文本字面量再 ::vector 转换，无需注册驱动适配器
        literal = "[" + ",".join(repr(v) for v in vector) + "]"
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, source, work, text FROM corpus_chunks"
                    " WHERE embedding <=> CAST(:vector AS vector) <= :max_distance"
                    " ORDER BY embedding <=> CAST(:vector AS vector) LIMIT :k"
                ),
                {"vector": literal, "max_distance": self._max_distance, "k": k},
            ).all()
        return [
            CorpusChunk(id=row.id, source=row.source, work=row.work, text=row.text)
            for row in rows
        ]
