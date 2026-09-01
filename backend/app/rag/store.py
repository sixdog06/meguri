"""CorpusStore 实现（#8）：内存 fake + pgvector live——同一检索语义。

混合检索（混合检索流水线，稠密+稀疏+RRF 融合）：
- 稠密路：注入的 EmbeddingProvider 向量化，余弦相似度 + min_score 阈值；
- 稀疏路：pg_trgm 词相似度（word_similarity，短查询在长文本内的最大局部
  重合度——站名等精确实体的字面匹配），阈值 TRGM_WORD_MIN_SIMILARITY；
- 融合：RRF（reciprocal rank fusion，只看排名不看原始分数，k=RRF_K）。
两路都先按 work 硬过滤（跨作品错配防线不经过融合层）。
fake 与 live 只是存储介质不同（内存 dict vs pgvector 表），语义不分叉。
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.adapters.ports import CorpusChunk, EmbeddingProvider
from app.models import CorpusChunkRow
from app.rag.embedding import DEFAULT_MIN_SCORE, HashEmbeddingProvider, cosine_similarity

#: RRF 平滑常数（压低头部名次差距；参考实现常用 60）
RRF_K = 60
#: 稀疏路词相似度阈值（查询词 trigram 在文本内的包含度；按 eval 数据集标定）
TRGM_WORD_MIN_SIMILARITY = 0.5


def _trigrams(s: str) -> set[str]:
    """pg_trgm 兼容的归一化：字符串首尾补空格后取全部连续 3 字符窗口的集合。"""
    s = "  " + s + " "
    return {s[i : i + 3] for i in range(len(s) - 2)}


def _word_similarity(query: str, document: str) -> float:
    """短查询 vs 长文本：查询 trigram 与文本任一滑窗（长度≈查询）trigram 的
    最大 Jaccard——对应 PG 侧 word_similarity(query, document)（查询在文本
    连续片段内的最大重合度）。

    不能用整体 Jaccard：长文本的 trigram 基数会把短查询的分数稀释到过不了阈。
    内存版只在测试夹具的小语料上跑，O(n·m) 滑窗可接受。
    """
    tq = _trigrams(query)
    if not tq:
        return 0.0
    best = 0.0
    n = len(query)
    for size in range(max(1, n - 2), n + 3):
        for i in range(0, max(0, len(document) - size + 1)):
            tw = _trigrams(document[i : i + size])
            inter = len(tq & tw)
            union = len(tq) + len(tw) - inter
            if union:
                best = max(best, inter / union)
    return best


def _rrf_fuse(
    dense_ranked: list[str], sparse_ranked: list[str], k: int
) -> list[str]:
    """两路已排序 id 列表 → RRF 融合 top-k id。

    分数相同（两路各占一头名的常见情形）按稀疏路名次决胜：讲解场景的
    citation 里，站名的字面命中是比向量噪声更可靠的信号；再平则按 id
    字典序（确定性）。
    """
    scores: dict[str, float] = {}
    for ranked in (dense_ranked, sparse_ranked):
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    sparse_rank = {cid: r for r, cid in enumerate(sparse_ranked, start=1)}
    return sorted(
        scores,
        key=lambda i: (-scores[i], sparse_rank.get(i, len(sparse_ranked) + 1), i),
    )[:k]


class InMemoryCorpusStore:
    """内存版 fake：与 live 相同的混合检索语义（稠密哈希向量 + 稀疏包含度 + RRF）。"""

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

    def search(self, query: str, k: int, work: str | None = None) -> list[CorpusChunk]:
        """混合检索 top-k：稠密（余弦 ≥ min_score）+ 稀疏（trigram ≥ 阈值）
        两路召回，RRF 融合。work 非空时只在该作品的语料里检索（防跨作品错配）。"""
        query_vector = self._embedder.embed([query])[0]
        pool = [c for c in self._chunks.values() if work is None or c.work == work]
        m = max(k * 4, 20)  # 每路候选池上限（与 PG 版一致）

        dense = sorted(
            (
                (cosine_similarity(query_vector, self._vectors[c.id]), c.id)
                for c in pool
            ),
            key=lambda item: (-item[0], item[1]),
        )
        dense_ranked = [cid for score, cid in dense if score >= self._min_score][:m]

        sparse = sorted(
            ((_word_similarity(query, c.text), c.id) for c in pool),
            key=lambda item: (-item[0], item[1]),
        )
        sparse_ranked = [
            cid for score, cid in sparse if score >= TRGM_WORD_MIN_SIMILARITY
        ][:m]

        return [self._chunks[cid] for cid in _rrf_fuse(dense_ranked, sparse_ranked, k)]


class PgVectorCorpusStore:
    """pgvector 版 live 实现：向量存 corpus_chunks 表，SQL 层做两路召回 + RRF 融合。"""

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

    def search(self, query: str, k: int, work: str | None = None) -> list[CorpusChunk]:
        """混合检索 top-k（一条 SQL）：稠密路（HNSW 近似最近邻 + 余弦阈值）与
        稀疏路（GIN trigram 字面匹配）各自召回 m 个候选，RRF 融合排名。
        work 非空时两路都只在该作品的语料里检索（防跨作品错配）。"""
        vector = self._embedder.embed([query])[0]
        # psycopg 会把 list[float] 绑成 float8[]（没有到 vector 的 cast），
        # 传文本字面量再 ::vector 转换，无需注册驱动适配器
        literal = "[" + ",".join(repr(v) for v in vector) + "]"
        m = max(k * 4, 20)  # 每路候选池上限
        # work 过滤在 Python 侧拼接：":work IS NULL" 会让 psycopg 无法推断参数类型
        work_clause = " AND work = :work" if work is not None else ""
        params = {
            "vector": literal,
            "max_distance": self._max_distance,
            "q": query,
            "m": m,
            "k": k,
            "rrf_k": RRF_K,
            "work": work,
        }
        with self._engine.connect() as conn:
            # <%（词相似度）运算符的阈值：短查询在长文本内的最大局部重合度，
            # GIN 索引按它过滤（连接当前事务内生效）
            conn.execute(
                text("SELECT set_config('pg_trgm.word_similarity_threshold', :t, true)"),
                {"t": str(TRGM_WORD_MIN_SIMILARITY)},
            )
            rows = conn.execute(
                text(
                    "WITH dense AS ("
                    "  SELECT id, ROW_NUMBER() OVER ("
                    "    ORDER BY embedding <=> CAST(:vector AS vector)) AS r"
                    "  FROM corpus_chunks"
                    "  WHERE embedding <=> CAST(:vector AS vector) <= :max_distance"
                    f"{work_clause}"
                    "  LIMIT :m"
                    "), sparse AS ("
                    "  SELECT id, ROW_NUMBER() OVER ("
                    "    ORDER BY word_similarity(:q, text) DESC, id) AS r"
                    "  FROM corpus_chunks"
                    "  WHERE :q <% text"
                    f"{work_clause}"
                    "  LIMIT :m"
                    "), legs AS ("
                    "  SELECT id, r, NULL::bigint AS s_rank FROM dense"
                    "  UNION ALL SELECT id, r, r FROM sparse"
                    "), fused AS ("
                    "  SELECT id, SUM(1.0 / (:rrf_k + r)) AS score,"
                    "         MIN(s_rank) AS s_rank"
                    "  FROM legs GROUP BY id"
                    ")"
                    "SELECT c.id, c.source, c.work, c.text"
                    " FROM fused JOIN corpus_chunks c ON c.id = fused.id"
                    # 分数相同时按稀疏路名次决胜（字面命中 > 向量噪声），再按 id
                    " ORDER BY fused.score DESC, fused.s_rank NULLS LAST, c.id"
                    " LIMIT :k"
                ),
                params,
            ).all()
        return [
            CorpusChunk(id=row.id, source=row.source, work=row.work, text=row.text)
            for row in rows
        ]
