"""Policy retriever (grounding source for the drafter).

BM25 keyword retrieval over the FAQ / policy knowledge base. The corpus and the
BM25 index are built lazily on first use and cached on the instance, so the
JSON file is read and tokenised once. The `backend` flag and the
`RetrievedChunk` contract are the seams for swapping in vector retrieval later.

The knowledge base JSON (data/knowledge_base/policies.json) uses these fields
per chunk: id, category, title, content, source, tags. We index title +
content + tags and return chunks ranked by BM25 relevance.
"""

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

from app.core.config import settings

logger = logging.getLogger(__name__)

# data/knowledge_base/policies.json lives at the project root. This file is at
# backend/app/pipeline/retriever.py → parents[3] is the repo root.
_DEFAULT_KB_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "knowledge_base" / "policies.json"
)

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

    def __init__(self, backend: str = "bm25", kb_path: Path | None = None) -> None:
        self.backend = backend
        self._kb_path = kb_path or _DEFAULT_KB_PATH
        # Cached on first retrieve(); cleared by rebuild_index().
        self._policies: list[dict] | None = None
        self._index: BM25Okapi | None = None

    def _ensure_loaded(self) -> None:
        """Load policies and build the BM25 index if not already cached."""
        if self._index is not None and self._policies is not None:
            return
        with open(self._kb_path, encoding="utf-8") as fh:
            self._policies = json.load(fh)
        corpus = [
            _tokenize(
                f"{p.get('title', '')} {p.get('content', '')} "
                f"{' '.join(p.get('tags', []))}"
            )
            for p in self._policies
        ]
        self._index = BM25Okapi(corpus)

    def rebuild_index(self) -> None:
        """Clear the cache so the next retrieve() reloads from disk."""
        self._policies = None
        self._index = None

    @property
    def document_count(self) -> int:
        """Number of policy chunks in the corpus (loads the KB if needed)."""
        self._ensure_loaded()
        return len(self._policies or [])

    async def retrieve(
        self, query: str, intent: str, top_k: int = 3
    ) -> list[RetrievedChunk]:
        """Return up to ``top_k`` policy chunks most relevant to the query.

        The intent is appended to the query text so intent vocabulary
        influences ranking. Chunks scoring > 0 are preferred; if nothing
        scores above zero, the top_k highest-scored chunks are returned anyway
        as a fallback so the drafter always has some grounding context.
        """
        self._ensure_loaded()
        assert self._policies is not None and self._index is not None

        query_tokens = _tokenize(f"{query} {intent}")
        scores = self._index.get_scores(query_tokens)

        # Indices ranked by score, highest first.
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        positive = [i for i in ranked if scores[i] > 0]
        chosen = (positive or ranked)[:top_k]

        return [
            RetrievedChunk(
                policy_id=self._policies[i].get("id", ""),
                title=self._policies[i].get("title", ""),
                content=self._policies[i].get("content", ""),
                score=float(scores[i]),
                category=self._policies[i].get("category", ""),
                tags=self._policies[i].get("tags", []),
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
    Anything else raises ``ValueError``. Both expose the same async
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
    else:
        raise ValueError(
            f"Unknown RETRIEVAL_BACKEND {backend!r}; expected 'bm25' or 'faiss'."
        )

    _retriever_backend = backend
    logger.info("Retriever backend initialized: %s", backend)
    return _retriever_singleton
