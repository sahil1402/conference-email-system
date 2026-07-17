"""Tests for the trainable classifier backend and its training endpoint.

Artifact paths are redirected to a per-test ``tmp_path`` so the repo's
``backend/models/`` is never written, and the module singleton is reset between
tests. The embedder (all-MiniLM-L6-v2) is downloaded once and cached by
huggingface_hub, so the trained tests load it from disk on subsequent runs.
"""

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

import main
import app.pipeline.trainable_classifier as tcmod
from app.pipeline.trainable_classifier import TrainableClassifier

# Heavy ML module (embedding model loads/training) — deselected by -m 'not ml'.
pytestmark = pytest.mark.ml


def _samples() -> list[dict]:
    """10 synthetic labeled emails across 3 intents."""
    data: list[dict] = []
    for _ in range(4):
        data.append(
            {
                "subject": "Deadline",
                "body": "When is the paper submission deadline extension, AoE?",
                "intent": "submission_deadline",
            }
        )
        data.append(
            {
                "subject": "Formatting",
                "body": "page limit latex template formatting font two-column",
                "intent": "formatting_requirements",
            }
        )
    data.append(
        {
            "subject": "Withdraw",
            "body": "I want to withdraw my submission and retract the paper.",
            "intent": "submission_withdrawal",
        }
    )
    data.append(
        {
            "subject": "Withdrawal",
            "body": "Please process the withdrawal of our submission.",
            "intent": "submission_withdrawal",
        }
    )
    return data  # 10 samples, 3 distinct intents


@pytest.fixture
def tmp_models(monkeypatch, tmp_path):
    """Redirect artifact IO to tmp_path and reset the singleton for isolation."""
    monkeypatch.setattr(tcmod, "_MODELS_DIR", tmp_path)
    monkeypatch.setattr(tcmod, "_MODEL_FILE", tmp_path / "classifier.joblib")
    monkeypatch.setattr(tcmod, "_LABEL_FILE", tmp_path / "classifier_labels.joblib")
    monkeypatch.setattr(tcmod, "_INSTANCE", None)
    return tmp_path


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# TrainableClassifier unit behavior
# ---------------------------------------------------------------------------
def test_classify_without_model_falls_back_to_keyword(tmp_models):
    tc = TrainableClassifier()
    assert tc.clf is None  # nothing on disk
    result = tc.classify("Deadline question", "When is the submission deadline?")
    # Delegates to the keyword classifier and never loads the embedder.
    assert result.method == "keyword"
    assert tc.embedder is None
    assert result.intent in {"submission_deadline", "general_inquiry"}


def test_train_returns_metrics(tmp_models):
    tc = TrainableClassifier()
    result = tc.train(_samples())
    assert result["samples"] == 10
    assert isinstance(result["classes"], list)
    assert len(result["classes"]) >= 2
    assert isinstance(result["accuracy"], float)
    assert 0.0 <= result["accuracy"] <= 1.0
    # Artifacts were persisted to the redirected tmp dir.
    assert (tmp_models / "classifier.joblib").exists()


def test_classify_uses_trained_model_after_train(tmp_models):
    tc = TrainableClassifier()
    tc.train(_samples())
    result = tc.classify("Deadline", "what is the paper submission deadline extension")
    assert result.method == "trained_classifier"
    assert result.intent in {
        "submission_deadline",
        "formatting_requirements",
        "submission_withdrawal",
    }


# ---------------------------------------------------------------------------
# Training endpoint
# ---------------------------------------------------------------------------
async def test_train_endpoint_too_few_samples_422(client):
    payload = {
        "training_data": [
            {"subject": "s", "body": "b", "intent": "general_inquiry"}
            for _ in range(4)  # below the 5-sample minimum
        ]
    }
    resp = await client.post("/api/v1/train/classifier", json=payload)
    assert resp.status_code == 422


async def test_train_endpoint_valid_returns_trained(tmp_models, client):
    payload = {"training_data": _samples()}
    resp = await client.post("/api/v1/train/classifier", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "trained"
    assert body["samples"] == 10
    assert isinstance(body["classes"], list) and len(body["classes"]) >= 2
    assert isinstance(body["accuracy"], float)
    assert body["model_path"] == TrainableClassifier.MODEL_PATH
