"""process_email persists the retrieval context used to ground the draft."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.pipeline.orchestrator import EmailPipeline
from app.repositories.email_repository import EmailRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_process_email_persists_retrieval_context(session):
    pipeline = EmailPipeline()
    result = await pipeline.process_email(
        {
            "from": "author@uni.edu",
            "to": "chair@conf.org",
            "subject": "Question about the submission deadline",
            "body": "Can I get an extension on the paper submission deadline?",
            "timestamp": "",
        },
        session,
    )

    email = await EmailRepository().get_email_by_id(session, result.email_id)
    ctx = email.retrieval_context
    assert ctx is not None
    assert isinstance(ctx["query"], str) and ctx["query"]
    assert "intent" in ctx
    # The stored ids are exactly the top-k that grounded this draft (both derive
    # from the same retrieval call, so they match even when the set is empty).
    assert ctx["retrieved_ids"] == [c.policy_id for c in result.retrieved_chunks]
