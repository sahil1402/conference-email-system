"""Tests for the Reciprocal Rank Fusion retriever (Phase 5C).

RRF math is verified on a small hand-computed fixture: two mock retrievers with
known rankings, fused with the standard k=60, checked against scores worked out
by hand. No embedding model or database is touched — the underlying retrievers
are trivial async mocks. Wiring through get_retriever("fusion") is also checked.
"""

import pytest

import app.pipeline.retriever as retriever_module
from app.pipeline.fusion_retriever import FusionRetriever
from app.pipeline.retriever import PolicyRetriever, RetrievedChunk

# Heavy ML module (embedding model loads/training) — deselected by -m 'not ml'.
pytestmark = pytest.mark.ml

_RRF_K = 60


def _chunk(pid: str, tags: list[str] | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        policy_id=pid,
        title=f"Title {pid}",
        content=f"Content {pid}",
        score=0.0,
        category="cat",
        tags=tags or [],
    )


class _MockRetriever:
    """Returns a fixed ranked list regardless of query/top_k."""

    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self._chunks = chunks

    async def retrieve(self, query: str, intent: str, top_k: int = 3):
        return list(self._chunks)


def _rrf(*ranks: int) -> float:
    """Sum of 1/(k+rank) over the given 1-based ranks."""
    return sum(1.0 / (_RRF_K + r) for r in ranks)


# ---------------------------------------------------------------------------
# RRF math + ordering
# ---------------------------------------------------------------------------
async def test_rrf_scores_and_order_match_hand_calculation():
    # BM25:  A(1) B(2) C(3)      FAISS: A(1) B(2) D(3)
    bm25 = _MockRetriever([_chunk("A"), _chunk("B"), _chunk("C")])
    faiss = _MockRetriever([_chunk("A"), _chunk("B"), _chunk("D")])
    fusion = FusionRetriever(bm25, faiss, rrf_k=_RRF_K, candidate_pool=10)

    results = await fusion.retrieve("q", "intent", top_k=4)
    order = [c.policy_id for c in results]
    scores = {c.policy_id: c.score for c in results}

    # Hand-computed RRF scores:
    #   A = 1/61 + 1/61,  B = 1/62 + 1/62,  C = 1/63,  D = 1/63
    assert scores["A"] == pytest.approx(_rrf(1, 1))
    assert scores["B"] == pytest.approx(_rrf(2, 2))
    assert scores["C"] == pytest.approx(_rrf(3))
    assert scores["D"] == pytest.approx(_rrf(3))
    # A > B, then C/D tie at 1/63 and break by policy_id (C before D).
    assert order == ["A", "B", "C", "D"]


async def test_document_in_one_ranker_only_still_scored():
    # A appears only in FAISS; B only in BM25.
    bm25 = _MockRetriever([_chunk("B")])
    faiss = _MockRetriever([_chunk("A")])
    fusion = FusionRetriever(bm25, faiss, rrf_k=_RRF_K)
    results = await fusion.retrieve("q", "i", top_k=5)
    ids = {c.policy_id for c in results}
    assert ids == {"A", "B"}
    for c in results:
        assert c.score == pytest.approx(_rrf(1))


async def test_top_k_limits_fused_results():
    bm25 = _MockRetriever([_chunk("A"), _chunk("B"), _chunk("C")])
    faiss = _MockRetriever([_chunk("A"), _chunk("B"), _chunk("C")])
    fusion = FusionRetriever(bm25, faiss)
    results = await fusion.retrieve("q", "i", top_k=2)
    assert len(results) == 2


# ---------------------------------------------------------------------------
# Return type + metadata
# ---------------------------------------------------------------------------
async def test_returns_valid_retrieved_chunks():
    bm25 = _MockRetriever([_chunk("A"), _chunk("B")])
    faiss = _MockRetriever([_chunk("B"), _chunk("A")])
    fusion = FusionRetriever(bm25, faiss)
    results = await fusion.retrieve("q", "i", top_k=2)
    assert all(isinstance(c, RetrievedChunk) for c in results)
    for c in results:
        assert c.policy_id and c.title and c.content
        assert isinstance(c.score, float)


async def test_metadata_prefers_tagged_chunk():
    # BM25 carries tags; FAISS returns the same doc with tags=[]. Fused chunk
    # should keep the richer (tagged) metadata.
    bm25 = _MockRetriever([_chunk("A", tags=["deadline"])])
    faiss = _MockRetriever([_chunk("A", tags=[])])
    fusion = FusionRetriever(bm25, faiss)
    results = await fusion.retrieve("q", "i", top_k=1)
    assert results[0].tags == ["deadline"]


# ---------------------------------------------------------------------------
# Factory wiring
# ---------------------------------------------------------------------------
def test_get_retriever_wires_fusion(monkeypatch):
    from app.pipeline.faiss_retriever import FAISSRetriever

    monkeypatch.setattr(retriever_module.settings, "RETRIEVAL_BACKEND", "fusion")
    retriever_module._retriever_singleton = None
    retriever_module._retriever_backend = None
    try:
        retriever = retriever_module.get_retriever()
        assert isinstance(retriever, FusionRetriever)
        # Reuses one BM25 + one FAISS instance (no duplicate embedder).
        assert isinstance(retriever.bm25, PolicyRetriever)
        assert isinstance(retriever.faiss, FAISSRetriever)
    finally:
        retriever_module._retriever_singleton = None
        retriever_module._retriever_backend = None
