"""FAISS dense-vector policy retriever (swappable alternative to BM25).

A drop-in for the BM25 ``PolicyRetriever``: same async
``retrieve(query, intent, top_k) -> list[RetrievedChunk]`` contract and the same
``RetrievedChunk`` shape, so the orchestrator and drafter consume it unchanged.

Instead of keyword scoring, it embeds policy text with sentence-transformers
(all-MiniLM-L6-v2, CPU) and searches a FAISS ``IndexFlatIP`` index. Vectors are
L2-normalized so inner product == cosine similarity.

The index is built lazily (on the first ``retrieve`` or an explicit ``build``),
never at import time. Documents are loaded from the database via
``PolicyRepository`` using the retriever's own short-lived async session, so the
public ``retrieve`` signature stays sessionless like BM25's.
"""

import logging
from collections.abc import Callable

import faiss
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import async_session_factory
from app.pipeline.retriever import RetrievedChunk
from app.repositories.policy_repository import PolicyRepository

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"


class FAISSRetriever:
    """Dense-vector retriever over policy documents, backed by FAISS."""

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL_NAME,
        policy_repo: PolicyRepository | None = None,
        session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        self.model_name = model_name
        self._policy_repo = policy_repo or PolicyRepository()
        # Callable returning an async-session context manager. Injectable so
        # tests can avoid a real database connection.
        self._session_factory = session_factory or async_session_factory

        self._embedder = None  # lazy SentenceTransformer (CPU)
        self._index: faiss.Index | None = None
        # Parallel to index rows: per-position policy metadata for result hydration.
        self._docs: list[dict] = []

    # --- lazy resources ---------------------------------------------------
    def _get_embedder(self):
        """Lazy-load the sentence-transformers model (CPU only)."""
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer

            self._embedder = SentenceTransformer(self.model_name, device="cpu")
        return self._embedder

    async def _load_policies(self) -> list:
        """Fetch all policy documents via the repository in a short-lived session."""
        async with self._session_factory() as db:
            return await self._policy_repo.get_all_policies(db)

    def _encode(self, texts: list[str]) -> np.ndarray:
        """Encode texts to L2-normalized float32 vectors for inner-product search."""
        vectors = np.asarray(self._get_embedder().encode(texts), dtype="float32")
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)
        faiss.normalize_L2(vectors)
        return vectors

    # --- index lifecycle --------------------------------------------------
    async def build(self) -> None:
        """Load documents, encode them, and build the FAISS index."""
        policies = await self._load_policies()

        self._docs = [
            {
                "policy_id": getattr(p, "policy_key", "") or "",
                "title": getattr(p, "title", "") or "",
                "content": getattr(p, "content", "") or "",
                "category": getattr(p, "category", "") or "",
            }
            for p in policies
        ]

        if not self._docs:
            # Nothing to index — leave an empty index so retrieve() returns [].
            self._index = None
            logger.info("FAISS index build skipped: no policy documents found.")
            return

        texts = [f"{d['title']} {d['content']}".strip() for d in self._docs]
        embeddings = self._encode(texts)

        index = faiss.IndexFlatIP(embeddings.shape[1])
        index.add(embeddings)
        self._index = index
        logger.info("FAISS index built: %d documents indexed.", len(self._docs))

    async def rebuild_index(self) -> None:
        """Re-encode all documents and rebuild the index (after policy updates)."""
        self._index = None
        self._docs = []
        await self.build()

    async def _ensure_built(self) -> None:
        """Build the index on first use if it has not been built yet."""
        if self._index is None and not self._docs:
            await self.build()

    @property
    def index_built(self) -> bool:
        """Whether the FAISS index has been built this process."""
        return self._index is not None

    @property
    def document_count(self) -> int:
        """Number of documents currently indexed (0 until first build)."""
        return len(self._docs)

    # --- retrieval --------------------------------------------------------
    async def retrieve(
        self, query: str, intent: str, top_k: int = 3
    ) -> list[RetrievedChunk]:
        """Return up to ``top_k`` policy chunks most similar to the query."""
        await self._ensure_built()

        if self._index is None or not self._docs:
            logger.info(
                "FAISS retrieve: query=%r intent=%r top_k=%d → 0 results (empty index).",
                query,
                intent,
                top_k,
            )
            return []

        query_vec = self._encode([query])
        k = min(top_k, len(self._docs))
        scores, indices = self._index.search(query_vec, k)

        results: list[RetrievedChunk] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:  # FAISS pads with -1 when fewer than k neighbors exist
                continue
            doc = self._docs[int(idx)]
            results.append(
                RetrievedChunk(
                    policy_id=doc["policy_id"],
                    title=doc["title"],
                    content=doc["content"],
                    score=float(score),
                    category=doc["category"],
                    tags=[],
                )
            )

        logger.info(
            "FAISS retrieve: query=%r intent=%r top_k=%d → %d results.",
            query,
            intent,
            top_k,
            len(results),
        )
        return results
