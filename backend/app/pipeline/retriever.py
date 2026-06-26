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
from pathlib import Path

from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

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
