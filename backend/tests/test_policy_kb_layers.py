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


async def test_upsert_by_key_insert_handles_real_shaped_raw_dict_with_source(session):
    """Real policies.json rows carry a 'source' key (and _map_policy retains it,
    since 'source' is a valid PolicyDocument column). The INSERT branch must not
    pass it twice to PolicyDocument(**kwargs) — the explicit source= kwarg wins."""
    repo = PolicyRepository()

    raw = {
        "id": "policy_101",
        "title": "v1",
        "content": "a",
        "source": "AAAI-27 — x.md",
        "tags": ["t"],
    }
    assert await repo.upsert_by_key(session, raw, source="aaai_scrape") == "inserted"

    from sqlalchemy import select
    from app.db.models import PolicyDocument
    got = (
        await session.execute(select(PolicyDocument).where(PolicyDocument.policy_key == "policy_101"))
    ).scalar_one()
    assert got.visibility == "public"
    assert got.status == "active"
    assert got.source == "aaai_scrape"   # explicit source arg wins over raw dict's source
    assert got.title == "v1"
    assert got.tags == ["t"]


async def test_create_internal_and_retire(session):
    repo = PolicyRepository()

    row = await repo.create_internal(session, title="Deadline Extended!", content="now March 5", actor="1")
    assert row.policy_key == "int_deadline-extended"
    assert row.visibility == "internal" and row.status == "active"
    assert row.source == "chair:1"

    dup = await repo.create_internal(session, title="Deadline Extended!", content="again", actor="1")
    assert dup.policy_key == "int_deadline-extended-2"     # collision → counter

    retired = await repo.retire(session, "int_deadline-extended")
    assert retired.status == "inactive"
    assert await repo.retire(session, "does_not_exist") is None


async def test_list_filters_and_search(session):
    repo = PolicyRepository()
    session.add_all([
        PolicyDocument(policy_key="policy_1", title="Submission deadline", content="deadline info", visibility="public", status="active"),
        PolicyDocument(policy_key="int_x", title="Internal ruling", content="chair note", visibility="internal", status="active"),
        PolicyDocument(policy_key="policy_2", title="Old rule", content="retired", visibility="public", status="inactive"),
    ])
    await session.commit()

    assert {p.policy_key for p in await repo.list(session)} == {"policy_1", "int_x", "policy_2"}      # no filter → all
    assert {p.policy_key for p in await repo.list(session, status="active")} == {"policy_1", "int_x"}
    assert {p.policy_key for p in await repo.list(session, visibility="internal")} == {"int_x"}
    assert {p.policy_key for p in await repo.list(session, search="DEADLINE")} == {"policy_1"}          # case-insensitive


async def test_reactivate(session):
    repo = PolicyRepository()
    session.add(PolicyDocument(policy_key="int_y", title="t", content="c", visibility="internal", status="inactive"))
    await session.commit()
    row = await repo.reactivate(session, "int_y")
    assert row is not None and row.status == "active"
    assert await repo.reactivate(session, "missing") is None
