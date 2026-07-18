"""Policy retriever (grounding source for the drafter).

BM25 keyword retrieval over the FAQ / policy knowledge base. The corpus is
loaded from the DB (active rows in public/internal visibility, via
``PolicyRepository.list_for_index``) and the BM25 index is built lazily on
first use and cached on the instance, mirroring the FAISS retriever's
DB-backed pattern. The `backend` flag and the `RetrievedChunk` contract are
the seams for swapping in vector retrieval later.

Indexed documents carry: policy_key (id), category, title, content, tags. We
index title + content + tags and return chunks ranked by BM25 relevance.
"""

import logging

from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

from app.core.config import settings

logger = logging.getLogger(__name__)

# Minimal English stopword list removed before BM25 tokenisation.
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "or",
    "in", "for",
}


def _tokenize(text: str) -> list[str]:
    """Lowercase, whitespace-split, and drop stopwords."""
    return [tok for tok in text.lower().split() if tok and tok not in _STOPWORDS]


class RetrievedChunk(BaseModel):
    """A single policy chunk returned by the retriever, with its score."""

    policy_id: str = Field(..., description="Knowledge-base id of the chunk.")
    title: str = Field(..., description="Chunk title.")
    content: str = Field(..., description="Chunk body text.")
    score: float = Field(..., description="BM25 relevance score (>= 0).")
    category: str = Field(default="", description="Policy category.")
    tags: list[str] = Field(default_factory=list, description="Chunk tags.")


class PolicyRetriever:
    """BM25 retriever over the policy knowledge base (swappable backend)."""

    def __init__(
        self,
        backend: str = "bm25",
        policy_repo=None,
        session_factory=None,
    ) -> None:
        from app.db.database import async_session_factory
        from app.repositories.policy_repository import PolicyRepository

        self.backend = backend
        self._policy_repo = policy_repo or PolicyRepository()
        self._session_factory = session_factory or async_session_factory
        # Cached on first retrieve(); cleared by rebuild_index().
        self._policies: list[dict] | None = None
        self._index: BM25Okapi | None = None

    async def _ensure_loaded(self) -> None:
        """Load the active corpus from the DB and build the BM25 index (once)."""
        if self._index is not None and self._policies is not None:
            return
        async with self._session_factory() as db:
            rows = await self._policy_repo.list_for_index(db)
        self._policies = [
            {
                "id": r.policy_key or "",
                "title": r.title or "",
                "content": r.content or "",
                "category": r.category or "",
                "tags": r.tags or [],
            }
            for r in rows
        ]
        corpus = [
            _tokenize(f"{p['title']} {p['content']} {' '.join(p['tags'])}")
            for p in self._policies
        ]
        # rank_bm25 requires a non-empty corpus; guard the empty-KB case.
        self._index = BM25Okapi(corpus) if corpus else None

    def rebuild_index(self) -> None:
        """Clear the cache so the next retrieve() reloads from the DB."""
        self._policies = None
        self._index = None

    @property
    def document_count(self) -> int:
        """Number of documents currently indexed (0 until first load)."""
        return len(self._policies or [])

    async def retrieve(
        self, query: str, intent: str, top_k: int = 3
    ) -> list[RetrievedChunk]:
        """Return up to ``top_k`` active policy chunks most relevant to the query."""
        await self._ensure_loaded()
        if not self._policies or self._index is None:
            return []

        query_tokens = _tokenize(f"{query} {intent}")
        scores = self._index.get_scores(query_tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        positive = [i for i in ranked if scores[i] > 0]
        chosen = (positive or ranked)[:top_k]

        return [
            RetrievedChunk(
                policy_id=self._policies[i]["id"],
                title=self._policies[i]["title"],
                content=self._policies[i]["content"],
                score=float(scores[i]),
                category=self._policies[i]["category"],
                tags=self._policies[i]["tags"],
            )
            for i in chosen
        ]


# ---------------------------------------------------------------------------
# Retriever factory (the RETRIEVAL_BACKEND swap seam)
# ---------------------------------------------------------------------------
# Process-wide singleton. Rebuilt only if RETRIEVAL_BACKEND changes (so tests
# that flip the flag get a fresh instance, while normal runs build once).
_retriever_singleton: object | None = None
_retriever_backend: str | None = None


def get_retriever():
    """Return the configured retriever singleton.

    ``RETRIEVAL_BACKEND == "bm25"`` → ``PolicyRetriever`` (BM25, unchanged).
    ``RETRIEVAL_BACKEND == "faiss"`` → ``FAISSRetriever`` (dense vectors).
    ``RETRIEVAL_BACKEND == "fusion"`` → ``FusionRetriever`` (RRF over both).
    Anything else raises ``ValueError``. All expose the same async
    ``retrieve(query, intent, top_k) -> list[RetrievedChunk]`` contract.
    """
    global _retriever_singleton, _retriever_backend
    backend = settings.RETRIEVAL_BACKEND

    if _retriever_singleton is not None and _retriever_backend == backend:
        return _retriever_singleton

    if backend == "bm25":
        _retriever_singleton = PolicyRetriever(backend="bm25")
    elif backend == "faiss":
        # Imported lazily so faiss / sentence-transformers load only when the
        # FAISS backend is actually selected.
        from app.pipeline.faiss_retriever import FAISSRetriever

        _retriever_singleton = FAISSRetriever(model_name=settings.FAISS_MODEL_NAME)
    elif backend == "fusion":
        # Fusion reuses one BM25 + one FAISS instance (no duplicate embedder).
        # Both imported lazily so dense deps load only when fusion is selected.
        from app.pipeline.faiss_retriever import FAISSRetriever
        from app.pipeline.fusion_retriever import FusionRetriever

        _retriever_singleton = FusionRetriever(
            bm25_retriever=PolicyRetriever(backend="bm25"),
            faiss_retriever=FAISSRetriever(model_name=settings.FAISS_MODEL_NAME),
        )
    else:
        raise ValueError(
            f"Unknown RETRIEVAL_BACKEND {backend!r}; expected 'bm25', 'faiss', or 'fusion'."
        )

    _retriever_backend = backend
    logger.info("Retriever backend initialized: %s", backend)
    return _retriever_singleton
