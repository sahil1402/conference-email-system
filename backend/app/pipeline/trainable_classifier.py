"""Trainable intent classifier (drop-in backend for the keyword classifier).

A sentence-embedding + LogisticRegression model that is a behavioral drop-in for
the keyword classifier: same ``ClassificationResult`` contract, and an automatic
fallback to ``keyword_classify`` whenever no trained artifact is on disk. This
keeps ``CLASSIFIER_BACKEND=trainable`` safe to switch on before any training has
happened.

Everything runs on CPU. The embedder and the sklearn model are loaded lazily so
importing this module (and the keyword path) stays cheap.

Artifacts are persisted under ``backend/models/`` (resolved from this file's
location, not the cwd) via joblib. The class-level ``MODEL_PATH`` / ``LABEL_PATH``
strings are the human-facing paths surfaced in API responses.
"""

import logging
import os
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder

from app.pipeline.classifier import ClassificationResult, keyword_classify

logger = logging.getLogger(__name__)

# Absolute artifact locations, anchored to this file so they resolve correctly
# regardless of the process working directory (tests run from backend/).
#   .../backend/app/pipeline/trainable_classifier.py → parents[2] == backend/
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_MODELS_DIR = _BACKEND_DIR / "models"
_MODEL_FILE = _MODELS_DIR / "classifier.joblib"
_LABEL_FILE = _MODELS_DIR / "classifier_labels.joblib"


class TrainableClassifier:
    """Sentence-embedding + LogisticRegression classifier.

    Falls back to the keyword classifier if no model artifact is found on disk.
    """

    # Human-facing paths (what the API reports). Real IO uses the absolute
    # paths above so it works no matter the cwd.
    MODEL_PATH = "backend/models/classifier.joblib"
    LABEL_PATH = "backend/models/classifier_labels.joblib"
    EMBED_MODEL = "all-MiniLM-L6-v2"  # small, fast, no GPU needed

    def __init__(self):
        self.embedder = None  # lazy-loaded SentenceTransformer
        self.clf = None  # LogisticRegression, loaded from disk or None
        self.label_encoder = None
        self._load_if_exists()

    def _load_if_exists(self):
        """Load saved model artifacts if present; stay un-trained otherwise."""
        if _MODEL_FILE.exists() and _LABEL_FILE.exists():
            try:
                self.clf = joblib.load(_MODEL_FILE)
                self.label_encoder = joblib.load(_LABEL_FILE)
                logger.info("Loaded trained classifier from %s", _MODEL_FILE)
            except Exception as exc:  # noqa: BLE001 - corrupt artifact → fallback
                logger.warning(
                    "Failed to load classifier artifacts (%s); using keyword fallback.",
                    exc,
                )
                self.clf = None
                self.label_encoder = None

    def _get_embedder(self):
        """Lazy-load the SentenceTransformer (CPU) — only on first use."""
        if self.embedder is None:
            # Imported here so torch / sentence-transformers load only when the
            # trained backend actually runs.
            from sentence_transformers import SentenceTransformer

            self.embedder = SentenceTransformer(self.EMBED_MODEL, device="cpu")
        return self.embedder

    @staticmethod
    def _compose(subject: str, body: str) -> str:
        """Join subject + body into the single text the model embeds."""
        return f"{subject or ''}\n{body or ''}".strip()

    def classify(self, subject: str, body: str) -> ClassificationResult:
        """Classify one email.

        No trained model on disk → delegate to ``keyword_classify`` (no embedding
        model is loaded). Otherwise embed subject+body, predict, and report the
        result with ``method='trained_classifier'``.
        """
        if self.clf is None or self.label_encoder is None:
            return keyword_classify(subject, body)

        text = self._compose(subject, body)
        embedding = self._get_embedder().encode([text])
        probs = self.clf.predict_proba(embedding)[0]

        order = np.argsort(probs)[::-1]
        top_idx = int(order[0])
        classes = self.label_encoder.inverse_transform(self.clf.classes_)
        top_intent = str(classes[top_idx])
        confidence = float(probs[top_idx])

        secondary = [
            str(classes[int(i)]) for i in order[1:3] if probs[int(i)] > 0.0
        ]

        return ClassificationResult(
            intent=top_intent,
            confidence=confidence,
            reasoning=(
                f"Trained classifier predicted '{top_intent}' "
                f"(probability {confidence:.2f})."
            ),
            secondary_intents=secondary,
            method="trained_classifier",
        )

    def train(self, training_data: list[dict]) -> dict:
        """Fine-tune on labeled data and persist the model.

        ``training_data``: list of ``{"subject", "body", "intent"}``. Trains a
        LogisticRegression over sentence embeddings, saves the model + label
        encoder, and updates this instance in place so ``classify`` uses the new
        model immediately. Returns ``{"samples", "classes", "accuracy"}``.
        """
        texts = [self._compose(d.get("subject", ""), d.get("body", "")) for d in training_data]
        labels = [d["intent"] for d in training_data]

        embeddings = self._get_embedder().encode(texts)

        label_encoder = LabelEncoder()
        y = label_encoder.fit_transform(labels)

        clf = LogisticRegression(max_iter=1000, C=1.0)
        clf.fit(embeddings, y)

        # Train-set accuracy (a quick sanity signal, not a held-out metric).
        accuracy = float(clf.score(embeddings, y))

        os.makedirs(_MODELS_DIR, exist_ok=True)
        joblib.dump(clf, _MODEL_FILE)
        joblib.dump(label_encoder, _LABEL_FILE)

        # Update in place so subsequent classify() calls use the fresh model.
        self.clf = clf
        self.label_encoder = label_encoder

        return {
            "samples": len(training_data),
            "classes": [str(c) for c in label_encoder.classes_],
            "accuracy": accuracy,
        }


# Module-level singleton — instantiated once so the embedder is not reloaded on
# every request. Accessed via get_trainable_classifier().
_INSTANCE: TrainableClassifier | None = None


def get_trainable_classifier() -> TrainableClassifier:
    """Return the process-wide TrainableClassifier singleton."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = TrainableClassifier()
    return _INSTANCE
