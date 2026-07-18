"""Shared helper: build a DB-backed PolicyRetriever seeded from a KB JSON file.

``PolicyRetriever`` reads its corpus from the ``policy_documents`` table (via
an injectable ``session_factory``), mirroring the FAISS retriever's DB-backed
pattern — the old ``kb_path=`` constructor shortcut is gone. Eval scripts that
just want "BM25 over the KB JSON on disk" (no real DB involved) still need a
retriever, so this seeds a throwaway SQLite database from the JSON once and
hands back a retriever bound to it.

A temp **file** is used instead of ``sqlite+aiosqlite:///:memory:``: these
scripts seed the DB in one event loop (this module's ``asyncio.run`` call) and
then invoke ``retrieve()`` under one or more *separate* ``asyncio.run`` calls
later. An in-memory SQLite engine (even with ``StaticPool``) holds its one
aiosqlite connection open on the loop that created it, so reusing it from a
different loop breaks. A file-based DB has no such constraint — each engine
opens its own connection to the same file. The temp file is intentionally
left on disk for the life of the process; these are short-lived scripts and
the OS temp directory is cleaned up independently.
"""

import asyncio
import json
import tempfile
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base
from app.pipeline.retriever import PolicyRetriever
from app.repositories.policy_repository import PolicyRepository


def build_retriever_from_kb(kb_path) -> PolicyRetriever:
    """Return a BM25 ``PolicyRetriever`` whose corpus is ``kb_path``'s JSON.

    Seeds a temp-file SQLite DB with the KB rows (synchronously, via its own
    ``asyncio.run``) and returns a retriever whose ``session_factory`` opens
    sessions against that same file — safe to call ``retrieve()`` from any
    later event loop.
    """
    db_file = Path(tempfile.mkstemp(suffix=".db", prefix="kb_retriever_")[1])
    db_url = f"sqlite+aiosqlite:///{db_file}"

    async def _seed() -> None:
        engine = create_async_engine(db_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            policies = json.loads(Path(kb_path).read_text(encoding="utf-8"))
            async with factory() as db:
                await PolicyRepository().bulk_insert_policies(db, policies)
        finally:
            await engine.dispose()

    asyncio.run(_seed())

    def session_factory() -> AsyncSession:
        # A fresh engine per call keeps this decoupled from whichever event
        # loop is live when retrieve() eventually runs.
        engine = create_async_engine(db_url)
        return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()

    return PolicyRetriever(session_factory=session_factory)
