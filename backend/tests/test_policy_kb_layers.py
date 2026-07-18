import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, PolicyDocument
from app.repositories.policy_repository import PolicyRepository


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


async def test_policy_document_defaults_public_active(session):
    row = PolicyDocument(policy_key="policy_101", title="T", content="C")
    session.add(row)
    await session.commit()
    await session.refresh(row)
    assert row.visibility == "public"
    assert row.status == "active"


async def test_list_for_index_filters_status_and_visibility(session):
    repo = PolicyRepository()
    session.add_all([
        PolicyDocument(policy_key="policy_101", title="pub", content="c", visibility="public", status="active"),
        PolicyDocument(policy_key="int_x", title="int", content="c", visibility="internal", status="active"),
        PolicyDocument(policy_key="policy_102", title="off", content="c", visibility="public", status="inactive"),
    ])
    await session.commit()

    keys = {p.policy_key for p in await repo.list_for_index(session)}
    assert keys == {"policy_101", "int_x"}                       # inactive excluded

    pub_only = await repo.list_for_index(session, visibilities=("public",))
    assert {p.policy_key for p in pub_only} == {"policy_101"}    # internal excluded


async def test_upsert_by_key_updates_content_but_preserves_governance_fields(session):
    repo = PolicyRepository()

    assert await repo.upsert_by_key(session, {"id": "policy_101", "title": "v1", "content": "a"}, source="aaai_scrape") == "inserted"

    # a chair retires it (governance field)
    row = (await repo.list_for_index(session, visibilities=("public",)))[0]
    row.status = "inactive"
    await session.commit()

    # re-scrape changes the content
    assert await repo.upsert_by_key(session, {"id": "policy_101", "title": "v2", "content": "b"}, source="aaai_scrape") == "updated"

    from sqlalchemy import select
    from app.db.models import PolicyDocument
    got = (await session.execute(select(PolicyDocument).where(PolicyDocument.policy_key == "policy_101"))).scalar_one()
    assert got.title == "v2"            # content refreshed
    assert got.status == "inactive"     # governance field NOT resurrected
