"""`extraction` is served by the email API, with its NULL semantics intact.

The load-bearing distinction carried all the way from the DB layer:

  * ``extraction: null``            — the row was never examined (it predates
                                      the column). Nothing is known.
  * ``{"submission_number": null,   — the row WAS examined and no submission
     "method": "llm_distiller"}``     was found. That is a real finding.

Coercing the first into the second (or into ``{}``) at any layer destroys it,
so these tests assert through the REAL endpoints via ASGITransport rather than
calling ``_email_to_dict`` directly — a serializer can be correct while the
route around it is not.

SCOPE LIMIT: API shape only. No endpoint consumes or filters on `extraction`
yet, and the frontend does not deserialize it (see the Email interface in
frontend/src/types/index.ts, which has no `extraction` field as of this piece).
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.db.database import get_db
from app.db.models import Base, Email

QUEUE = "/api/v1/emails/queue"

# A fully populated extraction, exactly as the pipeline stores it.
_FULL = {
    "submission_number": "22336",
    "openreview_forum_id": "Ab3xY9kLm2",
    "authors": [
        {
            "name": "Jane Roe",
            "email": "jane@example.edu",
            "affiliation": "Example University",
        },
        {"name": "John Doe", "email": None, "affiliation": None},
    ],
    "method": "llm_distiller",
}

# Examined, nothing found — every field null but `method` says the LLM ran.
_EXAMINED_EMPTY = {
    "submission_number": None,
    "openreview_forum_id": None,
    "authors": [],
    "method": "llm_distiller",
}


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            yield session

    async with factory() as session:
        session.add_all(
            [
                Email(
                    sender="jane@example.edu",
                    subject="full",
                    body="b",
                    status="DRAFT_GENERATED",
                    routing={"lane": "human_review"},
                    classification={"intent": "desk_reject_appeal", "confidence": 0.9},
                    draft={"draft_text": "Hello"},
                    extraction=_FULL,
                ),
                Email(
                    sender="legacy@example.edu",
                    subject="legacy-null",
                    body="b",
                    status="DRAFT_GENERATED",
                    routing={"lane": "human_review"},
                    # extraction deliberately unset -> NULL, as for every row
                    # processed before the column existed.
                ),
                Email(
                    sender="empty@example.edu",
                    subject="examined-empty",
                    body="b",
                    status="DRAFT_GENERATED",
                    routing={"lane": "human_review"},
                    extraction=_EXAMINED_EMPTY,
                ),
            ]
        )
        await session.commit()

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    main.app.dependency_overrides.clear()
    await engine.dispose()


async def _by_subject(client) -> dict[str, dict]:
    response = await client.get(QUEUE)
    assert response.status_code == 200, response.text
    return {e["subject"]: e for e in response.json()["emails"]}


async def _detail(client, subject: str) -> dict:
    rows = await _by_subject(client)
    response = await client.get(f"/api/v1/emails/{rows[subject]['id']}")
    assert response.status_code == 200, response.text
    return response.json()["email"]


# ---------------------------------------------------------------------------
# Fully populated extraction survives the wire
# ---------------------------------------------------------------------------
async def test_queue_serializes_a_full_extraction(client):
    row = (await _by_subject(client))["full"]
    assert row["extraction"] == _FULL


async def test_detail_serializes_a_full_extraction(client):
    assert (await _detail(client, "full"))["extraction"] == _FULL


async def test_authors_survive_as_a_list_of_objects(client):
    """Nested author objects must not be flattened or stringified in transit."""
    authors = (await _by_subject(client))["full"]["extraction"]["authors"]
    assert isinstance(authors, list) and len(authors) == 2
    assert authors[0] == {
        "name": "Jane Roe",
        "email": "jane@example.edu",
        "affiliation": "Example University",
    }
    # A partial mention keeps its nulls rather than dropping the keys.
    assert authors[1] == {"name": "John Doe", "email": None, "affiliation": None}


async def test_method_reaches_the_client(client):
    """`method` is contract, not decoration — it separates a model answer from
    a regex guess, so a consumer cannot weigh the values without it."""
    assert (await _by_subject(client))["full"]["extraction"]["method"] == "llm_distiller"


# ---------------------------------------------------------------------------
# NULL vs examined-but-empty — the distinction must reach the client
# ---------------------------------------------------------------------------
async def test_null_extraction_serializes_as_null_not_an_empty_object(client):
    row = (await _by_subject(client))["legacy-null"]
    assert "extraction" in row, "the key must be present even when null"
    assert row["extraction"] is None
    assert row["extraction"] != {}


async def test_examined_but_empty_is_distinct_from_null(client):
    """THE distinction this piece exists to preserve.

    Both rows have no submission number, but only one of them was ever looked
    at. If some layer coerced null -> {} or {} -> null, these two assertions
    could not both hold.
    """
    rows = await _by_subject(client)
    never_looked = rows["legacy-null"]["extraction"]
    looked_found_nothing = rows["examined-empty"]["extraction"]

    assert never_looked is None
    assert looked_found_nothing is not None
    assert looked_found_nothing["submission_number"] is None
    assert looked_found_nothing["method"] == "llm_distiller"
    assert never_looked != looked_found_nothing


async def test_examined_but_empty_survives_the_detail_endpoint_too(client):
    extraction = (await _detail(client, "examined-empty"))["extraction"]
    assert extraction == _EXAMINED_EMPTY
    assert extraction is not None


async def test_null_extraction_survives_the_detail_endpoint_too(client):
    assert (await _detail(client, "legacy-null"))["extraction"] is None


# ---------------------------------------------------------------------------
# Purely additive
# ---------------------------------------------------------------------------
async def test_existing_pipeline_fields_are_unchanged(client):
    """classification/routing/draft pass through exactly as before."""
    row = (await _by_subject(client))["full"]
    assert row["classification"] == {
        "intent": "desk_reject_appeal",
        "confidence": 0.9,
    }
    assert row["routing"] == {"lane": "human_review"}
    assert row["draft"] == {"draft_text": "Hello"}


async def test_extraction_sits_beside_the_other_pipeline_outputs(client):
    row = (await _by_subject(client))["full"]
    for key in ("classification", "routing", "draft", "extraction"):
        assert key in row, key


async def test_schema_model_accepts_the_served_shape():
    """The schemas.py mirror must actually validate what the API serves.

    A mirror that has drifted from the wire format is worse than no mirror, so
    this pins them together at the one point that matters.
    """
    from app.models.schemas import ExtractionResult

    parsed = ExtractionResult.model_validate(_FULL)
    assert parsed.submission_number == "22336"
    assert parsed.openreview_forum_id == "Ab3xY9kLm2"
    assert parsed.method == "llm_distiller"
    assert parsed.authors[1].name == "John Doe"
    assert parsed.authors[1].email is None

    empty = ExtractionResult.model_validate(_EXAMINED_EMPTY)
    assert empty.submission_number is None
    assert empty.authors == []
