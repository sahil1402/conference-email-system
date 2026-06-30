"""Retrieval API (v1) — surface which retrieval backend is active.

Mounted under ``/api/v1``, so the public path is ``/api/v1/retrieval/info``.
Lets the frontend show whether BM25 or FAISS is serving grounding context.
"""

from fastapi import APIRouter

from app.core.config import settings
from app.pipeline.retriever import get_retriever

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.get("/info")
async def retrieval_info() -> dict:
    """Report the active retrieval backend and its index state.

    For BM25, ``model_name`` is null and the index is always considered built
    (it loads cheaply from the KB on demand). For FAISS, ``document_count`` and
    ``index_built`` reflect the lazily-built index — both are 0/false until the
    first ``retrieve`` (or an explicit build) populates it.
    """
    backend = settings.RETRIEVAL_BACKEND
    retriever = get_retriever()

    if backend == "faiss":
        return {
            "backend": "faiss",
            "document_count": retriever.document_count,
            "model_name": retriever.model_name,
            "index_built": retriever.index_built,
        }

    # BM25 (default).
    return {
        "backend": "bm25",
        "document_count": retriever.document_count,
        "model_name": None,
        "index_built": True,
    }
