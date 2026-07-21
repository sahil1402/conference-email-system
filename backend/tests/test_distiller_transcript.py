import pytest

from app.core.config import settings
from app.pipeline import distiller as distiller_mod
from app.pipeline.distiller import EmailDistiller


class _FakeResp:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.mark.asyncio
async def test_distill_non_local_returns_none_with_transcript(monkeypatch):
    """Back-compat: any non-local provider still returns None (transcript ignored)."""
    monkeypatch.setattr(settings, "MODEL_PROVIDER", "fallback")
    result = await EmailDistiller().distill("Subj", "Body", transcript="Requester: hi")
    assert result is None


@pytest.mark.asyncio
async def test_distill_transcript_reaches_model_and_parses(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_PROVIDER", "local")
    monkeypatch.setattr(settings, "LOCAL_MODEL_BASE_URL", "http://local/v1")
    monkeypatch.setattr(settings, "LOCAL_MODEL_NAME", "m")
    monkeypatch.setattr(settings, "LOCAL_MODEL_API_KEY", None)

    captured = {}

    async def _fake_post_chat(client, url, payload, headers):
        captured["payload"] = payload
        return _FakeResp("INTENT: cms_support\nCONFIDENCE: 0.9\nQUERY: portal upload error")

    monkeypatch.setattr(distiller_mod, "post_chat", _fake_post_chat)

    transcript = "Requester: How do I upload?\n\nSupport: Use the portal.\n\nRequester: It errors."
    result = await EmailDistiller().distill("Upload", "ignored-body", transcript=transcript)

    assert result is not None
    assert result.intent == "cms_support"
    user_msg = captured["payload"]["messages"][1]["content"]
    assert "It errors." in user_msg  # the full transcript is sent
    assert "ignored-body" not in user_msg  # single-body path not used
    assert "latest" in user_msg.lower()  # anchoring instruction present


@pytest.mark.asyncio
async def test_distill_single_body_path_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_PROVIDER", "local")
    monkeypatch.setattr(settings, "LOCAL_MODEL_BASE_URL", "http://local/v1")
    monkeypatch.setattr(settings, "LOCAL_MODEL_NAME", "m")
    monkeypatch.setattr(settings, "LOCAL_MODEL_API_KEY", None)

    captured = {}

    async def _fake_post_chat(client, url, payload, headers):
        captured["payload"] = payload
        return _FakeResp("INTENT: cms_support\nCONFIDENCE: 0.8\nQUERY: q")

    monkeypatch.setattr(distiller_mod, "post_chat", _fake_post_chat)
    await EmailDistiller().distill("Subj", "MyBody", transcript=None)
    user_msg = captured["payload"]["messages"][1]["content"]
    assert user_msg.startswith("Subject: Subj\nBody:\nMyBody")
