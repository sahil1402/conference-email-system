from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.models import Email, EmailThreadMessage
from app.pipeline.orchestrator import EmailPipeline


@pytest_asyncio.fixture
async def adb():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(adb, *, old_draft):
    email = Email(
        sender="a@x.com", sender_name="Al", subject="Upload help",
        body="How do I upload?", status="APPROVED",
        source="zendesk", zendesk_ticket_id=888,
        classification={"intent": "cms_support", "confidence": 0.8},
        routing={"lane": "human_review"},
        draft=old_draft,
    )
    adb.add(email)
    await adb.commit()
    await adb.refresh(email)
    adb.add_all([
        EmailThreadMessage(
            email_id=email.id, zendesk_comment_id=1, public=True,
            author_role="end-user", plain_body="How do I upload?",
            created_at=datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc),
        ),
        EmailThreadMessage(
            email_id=email.id, zendesk_comment_id=2, public=True,
            author_role="agent", plain_body="Use the portal.",
            created_at=datetime(2026, 7, 21, 10, 1, tzinfo=timezone.utc),
        ),
        EmailThreadMessage(
            email_id=email.id, zendesk_comment_id=3, public=True,
            author_role="end-user", plain_body="It still errors out.",
            created_at=datetime(2026, 7, 21, 10, 2, tzinfo=timezone.utc),
        ),
    ])
    await adb.commit()
    return email


@pytest.mark.asyncio
async def test_reprocess_preserves_prior_draft_in_history(adb):
    old_draft = {"draft_text": "Prior reply", "citations": ["policy_101"], "is_edited": True}
    email = await _seed(adb, old_draft=old_draft)
    messages = await EmailPipeline().email_repo.get_thread_messages(adb, str(email.id))

    await EmailPipeline().reprocess_email_with_thread(
        adb, email, messages, triggering_comment_ids=[3]
    )

    refreshed = await EmailPipeline().email_repo.get_email_by_id(adb, str(email.id))
    hist = refreshed.draft["history"]
    assert len(hist) == 1
    assert hist[0]["draft_text"] == "Prior reply"
    assert hist[0]["reason"] == "followup"
    assert hist[0]["triggering_comment_ids"] == [3]
    assert hist[0]["is_edited"] is True
    # Status moves back to the open-draft lifecycle state.
    assert refreshed.status == "DRAFT_GENERATED"


@pytest.mark.asyncio
async def test_reprocess_appends_to_existing_history(adb):
    old_draft = {
        "draft_text": "Second reply",
        "history": [{"draft_text": "First reply", "reason": "followup"}],
    }
    email = await _seed(adb, old_draft=old_draft)
    messages = await EmailPipeline().email_repo.get_thread_messages(adb, str(email.id))

    await EmailPipeline().reprocess_email_with_thread(adb, email, messages)

    refreshed = await EmailPipeline().email_repo.get_email_by_id(adb, str(email.id))
    texts = [h["draft_text"] for h in refreshed.draft["history"]]
    assert texts == ["First reply", "Second reply"]  # oldest → newest


@pytest.mark.asyncio
async def test_reprocess_with_no_prior_draft_starts_empty_history(adb):
    email = await _seed(adb, old_draft=None)
    messages = await EmailPipeline().email_repo.get_thread_messages(adb, str(email.id))
    await EmailPipeline().reprocess_email_with_thread(adb, email, messages)
    refreshed = await EmailPipeline().email_repo.get_email_by_id(adb, str(email.id))
    assert refreshed.draft["history"] == []
