"""Tests for GET /emails/{id}/thread — a ticket's stored conversation thread.

Mirrors the established endpoint-test pattern used by
``test_redraft_endpoint.py`` / ``test_reevaluate_endpoint.py``: a local
``client`` fixture spins up an in-memory async SQLite engine, overrides the
app's ``get_db`` dependency with a session factory bound to that same engine,
and wraps ``main.app`` in an ``ASGITransport``. Tests seed data through the
returned ``factory`` (a second session against the same in-memory DB) and
exercise the route through the HTTP client.
"""

from datetime import datetime, timezone

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.db.database import get_db
from app.db.models import Base, Email, EmailThreadMessage


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
        async with factory() as s:
            yield s

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, factory
    main.app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_ticket_with_thread(factory) -> int:
    async with factory() as s:
        email = Email(
            sender="a@x.com", subject="S", body="body", status="DRAFT_GENERATED",
            source="zendesk", zendesk_ticket_id=777,
        )
        s.add(email)
        await s.commit()
        await s.refresh(email)
        s.add_all([
            EmailThreadMessage(
                email_id=email.id, zendesk_comment_id=1, public=True,
                author_role="end-user", plain_body="First", html_body="<p>First</p>",
                created_at=datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc),
            ),
            EmailThreadMessage(
                email_id=email.id, zendesk_comment_id=2, public=False,
                author_role="agent", plain_body="Internal", html_body="<p>Internal</p>",
                created_at=datetime(2026, 7, 21, 10, 1, tzinfo=timezone.utc),
            ),
        ])
        await s.commit()
        return email.id


async def test_get_thread_returns_all_messages_oldest_first(client):
    c, factory = client
    email_id = await _seed_ticket_with_thread(factory)

    resp = await c.get(f"/api/v1/emails/{email_id}/thread")

    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert [m["comment_id"] for m in msgs] == [1, 2]  # oldest-first
    assert msgs[0]["public"] is True and msgs[1]["public"] is False  # incl. internal
    assert msgs[0]["author_role"] == "end-user"
    assert isinstance(msgs[0]["created_at"], str)  # ISO serialized
    assert msgs[0]["html_body"] == "<p>First</p>"  # sanitized HTML exposed


async def test_get_thread_404_for_unknown_email(client):
    c, _ = client
    resp = await c.get("/api/v1/emails/999999/thread")
    assert resp.status_code == 404


async def test_get_thread_empty_for_non_zendesk_email(client):
    c, factory = client
    async with factory() as s:
        email = Email(sender="a@x", subject="s", body="b", status="DRAFT_GENERATED")
        s.add(email)
        await s.commit()
        await s.refresh(email)
        email_id = email.id

    resp = await c.get(f"/api/v1/emails/{email_id}/thread")

    assert resp.status_code == 200
    assert resp.json()["messages"] == []


async def test_get_thread_sanitizes_malicious_html(client):
    c, factory = client
    async with factory() as s:
        email = Email(
            sender="a@x", subject="s", body="b", status="DRAFT_GENERATED",
            source="zendesk", zendesk_ticket_id=888,
        )
        s.add(email)
        await s.commit()
        await s.refresh(email)
        s.add(EmailThreadMessage(
            email_id=email.id, zendesk_comment_id=1, public=True,
            author_role="end-user", plain_body="hi",
            html_body='<p>hi</p><script>steal()</script>'
                      '<a href="javascript:x()" onclick="y()">l</a>',
            created_at=datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc),
        ))
        await s.commit()
        email_id = email.id

    resp = await c.get(f"/api/v1/emails/{email_id}/thread")
    html = resp.json()["messages"][0]["html_body"]
    assert "<script" not in html and "steal()" not in html  # block + content gone
    assert "onclick" not in html and "javascript:" not in html  # handler + proto gone
    assert "<p>hi</p>" in html  # formatting preserved


def test_sanitize_html_helper():
    from app.api.v1.emails import _sanitize_html

    assert _sanitize_html(None) is None
    assert _sanitize_html("") is None
    # script/style blocks dropped with their contents; formatting kept.
    assert _sanitize_html("<b>x</b><style>b{}</style>") == "<b>x</b>"
    assert _sanitize_html("<p onclick='e()'>t</p>") == "<p>t</p>"
    assert "javascript:" not in _sanitize_html('<a href="javascript:e()">t</a>')
    # Inline raster data: images are kept; svg / data-on-links are blocked.
    assert 'src="data:image/png;base64,AAA="' in _sanitize_html(
        '<img src="data:image/png;base64,AAA=">'
    )
    assert _sanitize_html('<img src="data:image/svg+xml;base64,PHN2Zz4=">') == "<img>"
    assert "data:" not in _sanitize_html('<a href="data:text/html,x">t</a>')
