"""The email serializer surfaces the redrafting flag for the live badge."""

import pytest

from app.api.v1.emails import _email_to_dict
from app.core.config import settings
from app.db.models import Email


def test_email_to_dict_includes_redrafting():
    e = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    e.redrafting = True
    e.retrieval_context = {"query": "q", "intent": "", "retrieved_ids": ["policy_1"]}
    d = _email_to_dict(e)
    assert d["redrafting"] is True
    assert d["retrieval_context"]["retrieved_ids"] == ["policy_1"]


# ---------------------------------------------------------------------------
# zendesk_ticket_url (Z2a) — built from ZENDESK_SUBDOMAIN + zendesk_ticket_id.
# ZENDESK_SUBDOMAIN is monkeypatched in each test so the outcome is deterministic
# regardless of the ambient .env value.
# ---------------------------------------------------------------------------
def test_zendesk_ticket_url_built_when_ticket_and_subdomain_present(monkeypatch):
    monkeypatch.setattr(settings, "ZENDESK_SUBDOMAIN", "aaai")
    e = Email(
        sender="a@b.com", subject="s", body="b", status="draft_generated",
        source="zendesk", zendesk_ticket_id=21567,
    )
    d = _email_to_dict(e)
    assert d["zendesk_ticket_url"] == "https://aaai.zendesk.com/agent/tickets/21567"


def test_zendesk_ticket_url_null_when_subdomain_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "ZENDESK_SUBDOMAIN", None)
    e = Email(
        sender="a@b.com", subject="s", body="b", status="draft_generated",
        source="zendesk", zendesk_ticket_id=21567,
    )
    d = _email_to_dict(e)  # must not raise
    assert d["zendesk_ticket_url"] is None


def test_zendesk_ticket_url_null_when_no_ticket_id(monkeypatch):
    # Subdomain configured, but a non-Zendesk row (e.g. toy_dataset) has no ticket.
    monkeypatch.setattr(settings, "ZENDESK_SUBDOMAIN", "aaai")
    e = Email(
        sender="a@b.com", subject="s", body="b", status="draft_generated",
        source="toy_dataset",
    )
    assert e.zendesk_ticket_id is None
    d = _email_to_dict(e)
    assert d["zendesk_ticket_url"] is None


def test_zendesk_ticket_url_null_when_subdomain_empty_string(monkeypatch):
    # Empty string is falsy → treated as unconfigured (Z2a truthiness check).
    monkeypatch.setattr(settings, "ZENDESK_SUBDOMAIN", "")
    e = Email(
        sender="a@b.com", subject="s", body="b", status="draft_generated",
        source="zendesk", zendesk_ticket_id=21567,
    )
    d = _email_to_dict(e)
    assert d["zendesk_ticket_url"] is None
