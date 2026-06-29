"""Tests for the drafter's provider selection and the model-health endpoint.

All HTTP is mocked — no real Anthropic or local-model calls are made. The
drafter must never raise: every provider path returns a ``DraftResponse``, and
local-endpoint failures degrade to a deterministic fallback draft.
"""

from types import SimpleNamespace

import httpx
import pytest_asyncio
from httpx import ASGITransport

import main
from app.pipeline import drafter as drafter_module
from app.pipeline.drafter import ResponseDrafter


# ---------------------------------------------------------------------------
# Lightweight stand-ins for the pipeline objects the drafter reads.
# ---------------------------------------------------------------------------
def _classification():
    return SimpleNamespace(intent="submission_deadline", confidence=0.82)


def _routing():
    return SimpleNamespace(lane="faq", reason="High-confidence FAQ match.")


def _chunks():
    return [
        SimpleNamespace(
            policy_id="policy_002",
            title="Submission Deadline",
            content="The deadline is specified in AoE time.",
        )
    ]


def _email():
    return {"from": "author@university.edu", "subject": "Deadline?", "body": "When?"}


async def _draft_with(drafter: ResponseDrafter):
    return await drafter.draft(_email(), _classification(), _chunks(), _routing())


# ---------------------------------------------------------------------------
# Fake httpx client used to simulate the local model endpoint.
# ---------------------------------------------------------------------------
class _RaisingClient:
    """An AsyncClient whose POST always raises a connection error."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        raise httpx.ConnectError("connection refused")


class _OkClient:
    """An AsyncClient returning a canned OpenAI-style chat completion."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *args, **kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {"message": {"content": "Per policy_002, the deadline is AoE."}}
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 18},
            },
        )


# ---------------------------------------------------------------------------
# Drafter provider-selection tests
# ---------------------------------------------------------------------------
async def test_fallback_provider_returns_nonempty_text():
    drafter = ResponseDrafter(provider="fallback")
    result = await _draft_with(drafter)
    assert isinstance(result.draft_text, str)
    assert result.draft_text.strip() != ""
    assert result.model_used == "none"


async def test_local_provider_connection_error_falls_back(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _RaisingClient)
    drafter = ResponseDrafter(provider="local")
    result = await _draft_with(drafter)
    # Degrades gracefully — no exception, fallback text, error captured.
    assert result.draft_text.strip() != ""
    assert result.model_used == "none"
    assert result.generation_metadata.get("error_type") == "ConnectError"
    assert result.generation_metadata.get("provider") == "local"


async def test_local_provider_success_parses_response(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _OkClient)
    drafter = ResponseDrafter(provider="local")
    result = await _draft_with(drafter)
    assert "deadline" in result.draft_text.lower()
    assert result.citations == ["policy_002"]
    # model_used reflects the configured local model name, never hardcoded.
    assert result.model_used == drafter_module.settings.LOCAL_MODEL_NAME
    assert result.generation_metadata.get("output_tokens") == 18


# ---------------------------------------------------------------------------
# Model-health endpoint test
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_model_health_returns_provider(client):
    resp = await client.get("/api/v1/health/model")
    assert resp.status_code == 200
    body = resp.json()
    assert "provider" in body
    assert body["status"] in {"configured", "unreachable"}
