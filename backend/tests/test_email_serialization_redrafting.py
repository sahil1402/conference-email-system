"""The email serializer surfaces the redrafting flag for the live badge."""

import pytest

from app.api.v1.emails import _email_to_dict
from app.db.models import Email


def test_email_to_dict_includes_redrafting():
    e = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    e.redrafting = True
    e.retrieval_context = {"query": "q", "intent": "", "retrieved_ids": ["policy_1"]}
    d = _email_to_dict(e)
    assert d["redrafting"] is True
    assert d["retrieval_context"]["retrieved_ids"] == ["policy_1"]
