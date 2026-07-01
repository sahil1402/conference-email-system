"""Confidence calibration (Phase 5B).

A reusable layer that maps a classifier's raw confidence to a probability that
is meaningful against the FAQ routing threshold. It sits *between* "raw
confidence" and "confidence used for routing" — it never changes a classifier's
intent prediction and never changes the routing threshold itself.

Two methods are supported:
- ``platt``   — logistic regression, raw score → P(correct). The default: with a
  small calibration set (tens of emails) Platt scaling is far more stable than
  isotonic, which overfits step functions to sparse data.
- ``isotonic``— isotonic (monotonic, non-parametric) regression.

Calibrators are fit separately per ``CLASSIFIER_BACKEND`` and persisted to
``backend/models/calibration_{backend}.joblib`` (mirroring the trainable
classifier's artifact pattern). ``get_calibrator(backend)`` is a singleton
factory that returns ``None`` when no artifact exists yet, so callers degrade to
raw confidence with no special-casing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

import joblib
import numpy as np

logger = logging.getLogger(__name__)

# Anchored to this file so artifacts resolve regardless of cwd (tests run from
# backend/). .../backend/app/pipeline/calibration.py → parents[2] == backend/.
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_MODELS_DIR = _BACKEND_DIR / "models"

VALID_METHODS = ("platt", "isotonic")


def artifact_path(backend: str) -> Path:
    """Absolute path of the calibration artifact for ``backend``."""
    return _MODELS_DIR / f"calibration_{backend}.joblib"


class ConfidenceCalibrator:
    """Maps a raw classifier confidence to P(intent is correct).

    ``fit`` learns the mapping from ``(raw_score, was_correct)`` pairs;
    ``calibrate`` applies it to a single raw score. An unfitted calibrator (or
    one persisted before fitting) is the identity function, so it is always safe
    to call.
    """

    def __init__(self, method: str = "platt") -> None:
        if method not in VALID_METHODS:
            raise ValueError(
                f"Unknown calibration method {method!r}; expected one of {VALID_METHODS}."
            )
        self.method = method
        self._model = None  # sklearn estimator, or None
        # Base rate used when the labels are degenerate (all correct / all wrong)
        # and no discriminative model can be fit.
        self._constant: float | None = None

    # --- fitting ----------------------------------------------------------
    def fit(
        self, raw_scores: Sequence[float], correct_labels: Sequence[int]
    ) -> "ConfidenceCalibrator":
        """Fit the calibration mapping from raw confidences to correctness.

        ``raw_scores``: the classifier's raw confidence for each email.
        ``correct_labels``: 1 if the predicted intent was correct, else 0.
        """
        X = np.asarray(raw_scores, dtype=float)
        y = np.asarray(correct_labels, dtype=int)
        if X.shape[0] != y.shape[0]:
            raise ValueError("raw_scores and correct_labels must be the same length.")
        if X.shape[0] == 0:
            raise ValueError("Cannot fit a calibrator on an empty dataset.")

        # Degenerate labels → nothing to discriminate; predict the base rate.
        if len(np.unique(y)) < 2:
            self._constant = float(y.mean())
            self._model = None
            return self

        if self.method == "platt":
            from sklearn.linear_model import LogisticRegression

            model = LogisticRegression(max_iter=1000)
            model.fit(X.reshape(-1, 1), y)
        else:  # isotonic
            from sklearn.isotonic import IsotonicRegression

            model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            model.fit(X, y)

        self._model = model
        self._constant = None
        return self

    # --- application ------------------------------------------------------
    def calibrate(self, raw_score: float) -> float:
        """Map one raw confidence to a calibrated probability in [0, 1]."""
        if self._constant is not None:
            return float(self._constant)
        if self._model is None:
            # Unfitted → identity (safe no-op).
            return float(raw_score)

        if self.method == "platt":
            p = float(self._model.predict_proba([[float(raw_score)]])[0, 1])
        else:
            p = float(self._model.predict([float(raw_score)])[0])
        return float(min(max(p, 0.0), 1.0))

    @property
    def is_fitted(self) -> bool:
        return self._model is not None or self._constant is not None

    # --- persistence ------------------------------------------------------
    def save(self, path: Path) -> None:
        """Persist the calibrator state via joblib."""
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {"method": self.method, "model": self._model, "constant": self._constant},
            path,
        )

    @classmethod
    def load(cls, path: Path) -> "ConfidenceCalibrator":
        """Load a calibrator persisted by :meth:`save`."""
        state = joblib.load(path)
        calibrator = cls(method=state["method"])
        calibrator._model = state["model"]
        calibrator._constant = state["constant"]
        return calibrator


# ---------------------------------------------------------------------------
# Reliability / calibration-quality metrics (pure; reused by script + tests)
# ---------------------------------------------------------------------------
def brier_score(probs: Sequence[float], labels: Sequence[int]) -> float:
    """Mean squared error between predicted probabilities and outcomes.

    Lower is better; a perfectly calibrated + confident predictor → 0.0.
    """
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=float)
    if p.shape[0] == 0:
        return 0.0
    return float(np.mean((p - y) ** 2))


def expected_calibration_error(
    probs: Sequence[float], labels: Sequence[int], n_bins: int = 10
) -> float:
    """Expected Calibration Error: mean |accuracy − confidence| over bins."""
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=float)
    n = p.shape[0]
    if n == 0:
        return 0.0
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p > lo) & (p <= hi) if i > 0 else (p >= lo) & (p <= hi)
        count = int(mask.sum())
        if count == 0:
            continue
        acc = float(y[mask].mean())
        conf = float(p[mask].mean())
        ece += (count / n) * abs(acc - conf)
    return float(ece)


def reliability_table(
    probs: Sequence[float], labels: Sequence[int], n_bins: int = 10
) -> list[dict]:
    """Bucket predictions into fixed deciles → (mean confidence vs accuracy).

    Returns one row per non-empty bucket; this is exactly the data a reliability
    diagram (Phase 5E) would plot.
    """
    p = np.asarray(probs, dtype=float)
    y = np.asarray(labels, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows: list[dict] = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p > lo) & (p <= hi) if i > 0 else (p >= lo) & (p <= hi)
        count = int(mask.sum())
        if count == 0:
            continue
        rows.append(
            {
                "bucket": f"{lo:.1f}-{hi:.1f}",
                "count": count,
                "mean_confidence": round(float(p[mask].mean()), 4),
                "accuracy": round(float(y[mask].mean()), 4),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Singleton factory (mirrors get_trainable_classifier / get_retriever)
# ---------------------------------------------------------------------------
_CALIBRATORS: dict[str, ConfidenceCalibrator] = {}


def get_calibrator(backend: str) -> ConfidenceCalibrator | None:
    """Return the fitted calibrator for ``backend``, or ``None`` if none exists.

    Cached per backend. Returns ``None`` (never raises) when no artifact is on
    disk or it fails to load, so callers can treat calibration as opt-in.
    """
    if backend in _CALIBRATORS:
        return _CALIBRATORS[backend]

    path = artifact_path(backend)
    if not path.exists():
        return None
    try:
        calibrator = ConfidenceCalibrator.load(path)
    except Exception as exc:  # noqa: BLE001 - corrupt artifact → no-op
        logger.warning("Failed to load calibrator for %s (%s); ignoring.", backend, exc)
        return None
    _CALIBRATORS[backend] = calibrator
    return calibrator


def reset_calibrator_cache() -> None:
    """Clear the calibrator cache (after fitting, or between tests)."""
    _CALIBRATORS.clear()


# ---------------------------------------------------------------------------
# Fit-from-ground-truth orchestration (shared by the CLI script + the endpoint)
# ---------------------------------------------------------------------------
# data/eval/ground_truth.json lives at the project root (backend/ is parents[2]
# from this file, so its parent is the repo root).
_DEFAULT_GROUND_TRUTH = _BACKEND_DIR.parent / "data" / "eval" / "ground_truth.json"

# Map a CLASSIFIER_BACKEND value to the calibration artifact key.
_BACKEND_KEYS = {"keyword": "keyword", "trainable": "trainable", "trained": "trainable"}


def backend_key(backend: str) -> str:
    """Normalize a classifier strategy to its calibration artifact key."""
    return _BACKEND_KEYS.get(backend, backend)


async def collect_calibration_pairs(
    backend: str, ground_truth_path: str | Path | None = None
) -> tuple[list[float], list[int], list[dict]]:
    """Run ``backend`` over the ground truth → (raw_scores, correct_labels, records).

    Uses the classifier's RAW confidence (``ClassificationResult.confidence`` is
    always the raw score; calibration only ever populates the separate
    ``calibrated_confidence`` field), so this is safe to call even while a stale
    calibrator artifact is on disk.
    """
    import json

    # Lazy import breaks the classifier ↔ calibration import cycle.
    from app.pipeline.classifier import IntentClassifier

    path = Path(ground_truth_path) if ground_truth_path else _DEFAULT_GROUND_TRUTH
    with open(path, encoding="utf-8") as fh:
        entries = json.load(fh)

    classifier = IntentClassifier(strategy=backend)
    raw_scores: list[float] = []
    labels: list[int] = []
    records: list[dict] = []
    for entry in entries:
        result = await classifier.classify(entry.get("body", ""), entry.get("subject", ""))
        correct = int(result.intent == entry.get("ground_truth_intent"))
        raw_scores.append(float(result.confidence))
        labels.append(correct)
        records.append(
            {
                "id": entry.get("id"),
                "raw_confidence": round(float(result.confidence), 4),
                "predicted_intent": result.intent,
                "ground_truth_intent": entry.get("ground_truth_intent"),
                "correct": bool(correct),
            }
        )
    return raw_scores, labels, records


def fit_calibrator_for_backend(
    backend: str,
    method: str = "platt",
    ground_truth_path: str | Path | None = None,
) -> dict:
    """Fit + persist a calibrator for ``backend`` and return a reliability report.

    Runs the classifier over the ground-truth set, fits the calibrator, saves the
    artifact, resets the singleton cache, and returns before/after reliability
    tables plus Brier / ECE so callers (CLI or API) can show the miscalibration.
    """
    import asyncio

    key = backend_key(backend)
    raw_scores, labels, _ = asyncio.run(
        collect_calibration_pairs(backend, ground_truth_path)
    )

    calibrator = ConfidenceCalibrator(method=method).fit(raw_scores, labels)
    calibrated = [calibrator.calibrate(s) for s in raw_scores]

    path = artifact_path(key)
    calibrator.save(path)
    reset_calibrator_cache()

    return {
        "backend": key,
        "method": method,
        "samples": len(raw_scores),
        "n_correct": int(sum(labels)),
        "classification_accuracy": round(sum(labels) / len(labels), 4) if labels else 0.0,
        "brier_before": round(brier_score(raw_scores, labels), 4),
        "brier_after": round(brier_score(calibrated, labels), 4),
        "ece_before": round(expected_calibration_error(raw_scores, labels), 4),
        "ece_after": round(expected_calibration_error(calibrated, labels), 4),
        "reliability_before": reliability_table(raw_scores, labels),
        "reliability_after": reliability_table(calibrated, labels),
        "artifact_path": str(path),
    }
