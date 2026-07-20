"""Reciprocal Rank Fusion retriever (BM25 + FAISS).

A third swappable retrieval backend that fuses the rankings of the two existing
retrievers instead of scoring documents itself. It is a true drop-in: same async
``retrieve(query, intent, top_k) -> list[RetrievedChunk]`` contract and the same
``RetrievedChunk`` shape as ``PolicyRetriever`` and ``FAISSRetriever``.

Fusion uses Reciprocal Rank Fusion (RRF): each document's fused score is the sum
over every ranker of ``1 / (k + rank)``, where ``rank`` is the document's 1-based
position in that ranker's result list. ``k`` (default 60) is the standard RRF
constant from Cormack et al. (2009); a larger ``k`` flattens the contribution of
top ranks, and 60 is the widely used default that works well without tuning.

The two underlying retrievers are injected (reused), so the embedding model is
never loaded twice. Both are run sequentially — the knowledge base is tiny, so
concurrency would add complexity for no measurable gain.
"""

import logging

from app.pipeline.retriever import RetrievedChunk

logger = logging.getLogger(__name__)

# Standard RRF constant (Cormack et al., 2009). Higher = flatter rank weighting.
_DEFAULT_RRF_K = 60
# How many candidates to pull from each ranker before fusing. Larger than the
# usual top_k so a document ranked highly by only ONE ranker can still surface.
_DEFAULT_CANDIDATE_POOL = 10


class FusionRetriever:
    """Fuses BM25 + FAISS rankings via Reciprocal Rank Fusion."""

    def __init__(
        self,
        bm25_retriever,
        faiss_retriever,
        rrf_k: int = _DEFAULT_RRF_K,
        candidate_pool: int = _DEFAULT_CANDIDATE_POOL,
    ) -> None:
        # Reuse existing retriever instances — do NOT reinstantiate embedding models.
        self.bm25 = bm25_retriever
        self.faiss = faiss_retriever
        self.rrf_k = rrf_k
        self.candidate_pool = candidate_pool

    @property
    def document_count(self) -> int:
        """Number of documents available to fuse (shared KB → BM25's corpus)."""
        return self.bm25.document_count

    async def rebuild_index(self) -> None:
        """Clear both wrapped rankers so the next retrieve() reloads the KB."""
        self.bm25.rebuild_index()          # BM25 clear is synchronous
        await self.faiss.rebuild_index()   # FAISS re-encode is async

    @staticmethod
    def _rrf_contribution(rank: int, rrf_k: int) -> float:
        """RRF contribution of a document at 1-based ``rank`` for one ranker."""
        return 1.0 / (rrf_k + rank)

    async def retrieve(
        self, query: str, intent: str, top_k: int = 3
    ) -> list[RetrievedChunk]:
        """Return up to ``top_k`` policy chunks by fused RRF score.

        Pulls a candidate pool from each underlying retriever, sums the RRF
        contributions per document, and returns the top_k by fused score. Ties
        break by policy_id for deterministic ordering.
        """
        pool = max(top_k, self.candidate_pool)
        bm25_results = await self.bm25.retrieve(query, intent, top_k=pool)
        faiss_results = await self.faiss.retrieve(query, intent, top_k=pool)

        fused_scores: dict[str, float] = {}
        # Metadata source per document (title/content/category/tags/intents for
        # hydration). intents is DB-sourced identically by both rankers (no
        # asymmetry like tags historically had), so it just carries through
        # whichever chunk is kept below.
        chunk_by_id: dict[str, RetrievedChunk] = {}

        for results in (bm25_results, faiss_results):
            for rank, chunk in enumerate(results, start=1):
                pid = chunk.policy_id
                fused_scores[pid] = fused_scores.get(pid, 0.0) + self._rrf_contribution(
                    rank, self.rrf_k
                )
                # Keep the richest metadata: prefer a chunk that carries tags
                # (BM25) over one that does not (FAISS returns tags=[]).
                existing = chunk_by_id.get(pid)
                if existing is None or (not existing.tags and chunk.tags):
                    chunk_by_id[pid] = chunk

        # Sort by fused score desc, then policy_id asc for a stable, deterministic order.
        ranked = sorted(fused_scores.items(), key=lambda kv: (-kv[1], kv[0]))

        results: list[RetrievedChunk] = []
        for pid, score in ranked[:top_k]:
            base = chunk_by_id[pid]
            results.append(
                RetrievedChunk(
                    policy_id=base.policy_id,
                    title=base.title,
                    content=base.content,
                    score=float(score),
                    category=base.category,
                    tags=base.tags,
                    intents=base.intents,
                )
            )

        logger.info(
            "Fusion retrieve: query=%r intent=%r top_k=%d → %d results "
            "(bm25=%d, faiss=%d candidates, rrf_k=%d).",
            query,
            intent,
            top_k,
            len(results),
            len(bm25_results),
            len(faiss_results),
            self.rrf_k,
        )
        return results
