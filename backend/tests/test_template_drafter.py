"""Tests for the zero-AI template drafter (Phase 5D).

Verifies the template drafter fills responses from retrieved policy text
verbatim, uses per-intent openings, refuses to fabricate when nothing is
retrieved, and never makes a network call. Also checks it is a true drop-in
behind MODEL_PROVIDER=template and that /health/model reports it as healthy.
"""

from types import SimpleNamespace

import httpx
import pytest_asyncio
from httpx import ASGITransport

import main
from app.pipeline import drafter as drafter_module
from app.pipeline.drafter import ResponseDrafter
from app.pipeline.retriever import RetrievedChunk
from app.pipeline.template_drafter import _OPENINGS, TemplateDrafter


def _chunks() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            policy_id="policy_002",
            title="Full Paper Submission Deadline",
            content="The full paper deadline is specified in Anywhere on Earth (AoE) time.",
            score=1.0,
            category="submission_deadlines",
            tags=["deadline"],
        ),
        RetrievedChunk(
            policy_id="policy_001",
            title="Abstract Registration Deadline",
            content="All submissions must register a title and abstract by the abstract deadline.",
            score=0.8,
            category="submission_deadlines",
            tags=["abstract"],
        ),
    ]


def _email() -> dict:
    return {"from": "author@university.edu", "subject": "Deadline?", "body": "When is it?"}


# ---------------------------------------------------------------------------
# TemplateDrafter behavior
# ---------------------------------------------------------------------------
def test_output_contains_chunk_text_verbatim():
    result = TemplateDrafter().draft(_email(), "submission_requirements", _chunks())
    for chunk in _chunks():
        assert chunk.content in result.draft_text  # verbatim, not paraphrased
    assert result.citations == ["policy_002", "policy_001"]
    assert result.model_used == "template"
    assert result.generation_metadata["grounded"] is True


def test_opening_line_differs_by_intent():
    email = _email()
    chunks = _chunks()
    requirements = TemplateDrafter().draft(email, "submission_requirements", chunks).draft_text
    anonymity = TemplateDrafter().draft(email, "anonymity_violation", chunks).draft_text
    # Each starts with its own hand-written opening.
    assert requirements.startswith(_OPENINGS["submission_requirements"])
    assert anonymity.startswith(_OPENINGS["anonymity_violation"])
    assert _OPENINGS["submission_requirements"] != _OPENINGS["anonymity_violation"]


def test_all_fourteen_intents_have_openings():
    from app.pipeline.classifier import VALID_INTENTS

    assert set(_OPENINGS) == set(VALID_INTENTS)


def test_no_chunks_returns_human_review_and_never_fabricates():
    result = TemplateDrafter().draft(_email(), "submission_requirements", [])
    assert result.generation_metadata["grounded"] is False
    assert result.generation_metadata["reason"] == "no_policy_chunks"
    assert result.citations == []
    # Routes to a human — does not invent a policy answer.
    assert "program chair" in result.draft_text.lower()
    assert "policy_" not in result.draft_text


def test_unknown_intent_uses_default_opening():
    result = TemplateDrafter().draft(_email(), "not_a_real_intent", _chunks())
    assert result.draft_text.startswith("Thank you for contacting the program committee.")


# ---------------------------------------------------------------------------
# Drop-in via ResponseDrafter dispatch + no network
# ---------------------------------------------------------------------------
class _ExplodingClient:
    """httpx.AsyncClient stand-in that fails if the template path touches the network."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("template drafter must not make a network call")


async def test_template_provider_makes_no_network_call(monkeypatch):
    monkeypatch.setattr(drafter_module.httpx, "AsyncClient", _ExplodingClient)
    drafter = ResponseDrafter(provider="template")
    result = await drafter.draft(
        _email(),
        SimpleNamespace(intent="submission_requirements", confidence=0.9),
        _chunks(),
        SimpleNamespace(lane="faq", reason="FAQ match."),
    )
    # Reached here → no network call attempted. Lane is stamped for parity.
    assert result.model_used == "template"
    assert result.generation_metadata["provider"] == "template"
    assert result.generation_metadata["lane"] == "faq"
    assert "Anywhere on Earth" in result.draft_text


# ---------------------------------------------------------------------------
# Model-health endpoint reports template as always-healthy
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_reports_template_healthy(client, monkeypatch):
    monkeypatch.setattr(main.settings, "MODEL_PROVIDER", "template")
    resp = await client.get("/api/v1/health/model")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "template"
    assert body["status"] == "healthy"
