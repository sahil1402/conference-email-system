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

    content = (
        "=== REPLY ===\n"
        "Per the submission instructions, the deadline is AoE.\n"
        "=== CITATIONS ===\n"
        "policy_002\n"
        "=== NOTES FOR CHAIR ===\n"
        "none"
    )

    async def post(self, url, *args, **kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": type(self).content}}],
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
    # Structured sections are split out: "none" notes → None, no ids in reply.
    assert result.notes_for_chair is None
    assert "policy_002" not in result.draft_text
    # model_used reflects the configured local model name, never hardcoded.
    assert result.model_used == drafter_module.settings.LOCAL_MODEL_NAME
    assert result.generation_metadata.get("output_tokens") == 18


# ---------------------------------------------------------------------------
# Structured reply / notes_for_chair split + reply hygiene (Phase 7E)
# ---------------------------------------------------------------------------
async def test_notes_for_chair_split_out_of_reply(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _OkClient)
    monkeypatch.setattr(
        _OkClient,
        "content",
        "=== REPLY ===\nDear author, the deadline has passed.\n"
        "=== CITATIONS ===\npolicy_003, policy_005\n"
        "=== NOTES FOR CHAIR ===\nConfirm the cycle year before sending.",
    )
    result = await _draft_with(ResponseDrafter(provider="local"))
    assert result.draft_text == "Dear author, the deadline has passed."
    assert result.notes_for_chair == "Confirm the cycle year before sending."
    assert result.citations == ["policy_003", "policy_005"]
    assert "Confirm the cycle" not in result.draft_text


async def test_inline_policy_ids_are_scrubbed_from_reply(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _OkClient)
    monkeypatch.setattr(
        _OkClient,
        "content",
        "=== REPLY ===\nDraft reply:\nAuthors cannot be added (policy_187). "
        "The freeze is July 28 per policy_117.\n"
        "=== CITATIONS ===\npolicy_187\n"
        "=== NOTES FOR CHAIR ===\nnone",
    )
    result = await _draft_with(ResponseDrafter(provider="local"))
    # Scaffold header and every internal id are gone, even mid-sentence ones.
    assert "policy_" not in result.draft_text
    assert "Draft reply:" not in result.draft_text
    assert result.draft_text.startswith("Authors cannot be added.")
    assert result.citations == ["policy_187"]


async def test_unstructured_output_falls_back_gracefully(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _OkClient)
    monkeypatch.setattr(
        _OkClient, "content", "Per policy_002, the deadline is AoE."
    )
    result = await _draft_with(ResponseDrafter(provider="local"))
    # Legacy/plain output: citations recovered, ids still scrubbed from text.
    assert result.citations == ["policy_002"]
    assert "policy_" not in result.draft_text
    assert "deadline is AoE" in result.draft_text
    assert result.notes_for_chair is None


class _CapturingClient(_OkClient):
    """OK client that also records the kwargs of the last POST."""

    last_post: dict = {}

    async def post(self, url, *args, **kwargs):
        _CapturingClient.last_post = {"url": url, **kwargs}
        return await super().post(url, *args, **kwargs)


def _system_content() -> str:
    payload = _CapturingClient.last_post["json"]
    return payload["messages"][0]["content"]


# ---------------------------------------------------------------------------
# Chair-placeholder contract (Phase 7F)
# ---------------------------------------------------------------------------
async def test_chair_placeholders_extracted_and_preserved(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _OkClient)
    monkeypatch.setattr(
        _OkClient,
        "content",
        "=== REPLY ===\nYou can update the affiliation by "
        "[CHAIR: fill in the CRC update procedure]. The deadline is "
        "[CHAIR: confirm the camera-ready deadline].\n"
        "=== CITATIONS ===\nnone\n"
        "=== NOTES FOR CHAIR ===\nCRC update procedure is not in the corpus.",
    )
    result = await _draft_with(ResponseDrafter(provider="local"))
    # Tokens stay verbatim in the reply for the chair to edit in place...
    assert "[CHAIR: fill in the CRC update procedure]" in result.draft_text
    # ...and are surfaced as structured hints for the lane/approve gates.
    assert result.placeholders == [
        "fill in the CRC update procedure",
        "confirm the camera-ready deadline",
    ]


async def test_clean_reply_has_no_placeholders_or_leaks(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _OkClient)
    result = await _draft_with(ResponseDrafter(provider="local"))
    assert result.placeholders == []
    assert "reply_leaks" not in result.generation_metadata


async def test_leaked_meta_language_is_flagged_not_rewritten(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _OkClient)
    monkeypatch.setattr(
        _OkClient,
        "content",
        "=== REPLY ===\nThe policy context does not specify the withdrawal "
        "procedure. We will look into this and follow up.\n"
        "=== CITATIONS ===\nnone\n"
        "=== NOTES FOR CHAIR ===\nnone",
    )
    result = await _draft_with(ResponseDrafter(provider="local"))
    # The reply is left intact (flag, never regex-rewrite prose)...
    assert "does not specify" in result.draft_text
    # ...but every leak is recorded and surfaced to the chair as a warning.
    leaks = result.generation_metadata["reply_leaks"]
    assert any("not specify" in leak for leak in leaks)
    assert any("look into" in leak for leak in leaks)
    assert any("policy context" in leak for leak in leaks)
    assert result.notes_for_chair is not None
    assert "WARNING" in result.notes_for_chair


async def test_leak_warning_appends_to_existing_notes(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _OkClient)
    monkeypatch.setattr(
        _OkClient,
        "content",
        "=== REPLY ===\nWe will look into your account issue.\n"
        "=== CITATIONS ===\nnone\n"
        "=== NOTES FOR CHAIR ===\nVerify the account exists.",
    )
    result = await _draft_with(ResponseDrafter(provider="local"))
    # Model-written notes come first; the automated warning is appended.
    assert result.notes_for_chair.startswith("Verify the account exists.")
    assert "WARNING" in result.notes_for_chair


# ---------------------------------------------------------------------------
# Bearer-auth for hosted OpenAI-compatible endpoints (Phase 7D)
# ---------------------------------------------------------------------------
async def test_local_provider_sends_bearer_header_when_key_set(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _CapturingClient)
    monkeypatch.setattr(drafter_module.settings, "LOCAL_MODEL_API_KEY", "sk-test-key")
    result = await _draft_with(ResponseDrafter(provider="local"))
    assert result.model_used == drafter_module.settings.LOCAL_MODEL_NAME
    assert _CapturingClient.last_post["headers"] == {
        "Authorization": "Bearer sk-test-key"
    }


async def test_local_provider_sends_no_header_without_key(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _CapturingClient)
    monkeypatch.setattr(drafter_module.settings, "LOCAL_MODEL_API_KEY", None)
    await _draft_with(ResponseDrafter(provider="local"))
    assert _CapturingClient.last_post["headers"] is None


# ---------------------------------------------------------------------------
# Style-guide injection (Phase 7D)
# ---------------------------------------------------------------------------
async def test_style_guide_appended_to_system_prompt(tmp_path, monkeypatch):
    guide = tmp_path / "guide.md"
    guide.write_text("# Test Guide\nAlways use institutional we.", encoding="utf-8")
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _CapturingClient)
    monkeypatch.setattr(drafter_module.settings, "STYLE_GUIDE_PATH", str(guide))
    await _draft_with(ResponseDrafter(provider="local"))
    system = _system_content()
    # Grounding rules stay first; the guide is appended after them.
    assert system.startswith(drafter_module._SYSTEM_PROMPT)
    assert "Always use institutional we." in system


async def test_missing_style_guide_is_ignored(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _CapturingClient)
    monkeypatch.setattr(
        drafter_module.settings, "STYLE_GUIDE_PATH", "/nonexistent/guide.md"
    )
    result = await _draft_with(ResponseDrafter(provider="local"))
    # No exception, no guide — the base prompt is used unchanged.
    assert result.draft_text.strip() != ""
    assert _system_content() == drafter_module._SYSTEM_PROMPT


async def test_no_style_guide_by_default(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _CapturingClient)
    monkeypatch.setattr(drafter_module.settings, "STYLE_GUIDE_PATH", None)
    await _draft_with(ResponseDrafter(provider="local"))
    assert _system_content() == drafter_module._SYSTEM_PROMPT


class _ParamSwapClient(_OkClient):
    """400s on "max_tokens", succeeds once "max_completion_tokens" is used —
    mimics hosted chat-completions services that reject the legacy param."""

    calls: list = []

    async def post(self, url, *args, **kwargs):
        payload = kwargs.get("json", {})
        _ParamSwapClient.calls.append(dict(payload))
        if "max_tokens" in payload:
            return httpx.Response(
                400,
                request=httpx.Request("POST", url),
                json={"error": {"message": "Unsupported parameter: 'max_tokens'. "
                                           "Use 'max_completion_tokens' instead."}},
            )
        return await super().post(url, *args, **kwargs)


async def test_local_provider_swaps_to_max_completion_tokens(monkeypatch):
    _ParamSwapClient.calls = []
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _ParamSwapClient)
    result = await _draft_with(ResponseDrafter(provider="local"))
    # Second call carries the swapped parameter and succeeds — no fallback.
    assert len(_ParamSwapClient.calls) == 2
    assert "max_completion_tokens" in _ParamSwapClient.calls[1]
    assert "max_tokens" not in _ParamSwapClient.calls[1]
    assert "deadline" in result.draft_text.lower()


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


# ---------------------------------------------------------------------------
# Internal policy key scrubbing (Task 9)
# ---------------------------------------------------------------------------
def test_internal_keys_scrubbed_from_reply():
    text = "Per the deadline policy (int_deadline-extended), you may proceed."
    cleaned = drafter_module._INLINE_ID_RE.sub("", text)
    assert "int_deadline-extended" not in cleaned
    # existing behavior still holds
    assert "policy_" not in drafter_module._INLINE_ID_RE.sub("", "see policy_101 here")
