"""Tests for the confidence calibration layer (Phase 5B).

Part 1 covers the calibrator itself: fitting on a synthetic, deliberately
miscalibrated distribution and confirming the calibrated output tracks actual
correctness better than the raw input (Brier score + ECE both improve), plus
persistence and the graceful no-artifact factory.
"""

import numpy as np
import pytest

import app.pipeline.calibration as calibration_module
import app.pipeline.classifier as classifier_module
from app.core.config import settings
from app.pipeline.calibration import (
    ConfidenceCalibrator,
    artifact_path,
    brier_score,
    expected_calibration_error,
    get_calibrator,
    reliability_table,
    reset_calibrator_cache,
)
from app.pipeline.classifier import (
    ClassificationResult,
    IntentClassifier,
    keyword_classify,
)
from app.pipeline.router import EmailRouter

# A FAQ-eligible email that raw-scores below the 0.65 gate but classifies right.
_FAQ_SUBJECT = "Page limit before the deadline"
_FAQ_BODY = (
    "Is the page limit strict? I'm racing the deadline and need to know if the "
    "references count toward the page limit or not."
)


def _miscalibrated_dataset(n: int = 400, seed: int = 7):
    """A known-miscalibrated set: raw score is a squashed version of true P.

    True correctness probability is ``raw ** 0.5`` — i.e. a raw confidence of
    0.25 actually corresponds to ~0.5 accuracy, so the raw scores systematically
    understate correctness (exactly the Phase 5A symptom). Calibration should
    largely undo this.
    """
    rng = np.random.default_rng(seed)
    raw = rng.uniform(0.05, 0.95, size=n)
    true_p = np.sqrt(raw)  # monotonic but very different from raw
    labels = (rng.uniform(size=n) < true_p).astype(int)
    return raw.tolist(), labels.tolist()


# ---------------------------------------------------------------------------
# Core calibrator behavior
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["platt", "isotonic"])
def test_calibration_improves_brier_and_ece(method):
    raw, labels = _miscalibrated_dataset()
    cal = ConfidenceCalibrator(method=method).fit(raw, labels)
    calibrated = [cal.calibrate(s) for s in raw]

    brier_raw = brier_score(raw, labels)
    brier_cal = brier_score(calibrated, labels)
    ece_raw = expected_calibration_error(raw, labels)
    ece_cal = expected_calibration_error(calibrated, labels)

    # Calibrated predictions track actual correctness strictly better.
    assert brier_cal < brier_raw
    assert ece_cal < ece_raw


def test_calibrate_output_bounded():
    raw, labels = _miscalibrated_dataset()
    cal = ConfidenceCalibrator(method="platt").fit(raw, labels)
    for s in (-1.0, 0.0, 0.5, 1.0, 2.0):
        p = cal.calibrate(s)
        assert 0.0 <= p <= 1.0


def test_unfitted_calibrator_is_identity():
    cal = ConfidenceCalibrator(method="platt")
    assert cal.calibrate(0.42) == 0.42
    assert not cal.is_fitted


def test_degenerate_labels_predict_base_rate():
    # All-correct labels → no discriminative signal → predict the base rate 1.0.
    cal = ConfidenceCalibrator(method="platt").fit([0.3, 0.6, 0.9], [1, 1, 1])
    assert cal.calibrate(0.1) == 1.0
    assert cal.calibrate(0.9) == 1.0


def test_invalid_method_rejected():
    with pytest.raises(ValueError):
        ConfidenceCalibrator(method="sigmoid")


def test_fit_length_mismatch_rejected():
    with pytest.raises(ValueError):
        ConfidenceCalibrator().fit([0.1, 0.2], [1])


# ---------------------------------------------------------------------------
# Reliability metrics
# ---------------------------------------------------------------------------
def test_reliability_table_buckets():
    # Two clear buckets: low-confidence all-correct, high-confidence all-wrong.
    probs = [0.15, 0.15, 0.85, 0.85]
    labels = [1, 1, 0, 0]
    rows = reliability_table(probs, labels, n_bins=10)
    by_bucket = {r["bucket"]: r for r in rows}
    assert by_bucket["0.1-0.2"]["accuracy"] == 1.0
    assert by_bucket["0.8-0.9"]["accuracy"] == 0.0


def test_perfect_predictions_zero_brier():
    assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == 0.0


# ---------------------------------------------------------------------------
# Persistence + factory
# ---------------------------------------------------------------------------
def test_save_load_roundtrip(tmp_path):
    raw, labels = _miscalibrated_dataset()
    cal = ConfidenceCalibrator(method="platt").fit(raw, labels)
    path = tmp_path / "cal.joblib"
    cal.save(path)

    loaded = ConfidenceCalibrator.load(path)
    for s in (0.1, 0.4, 0.7, 0.95):
        assert loaded.calibrate(s) == pytest.approx(cal.calibrate(s))


def test_get_calibrator_missing_returns_none(monkeypatch, tmp_path):
    reset_calibrator_cache()
    # Point the artifact lookup at an empty temp dir → no artifact → None.
    monkeypatch.setattr(
        "app.pipeline.calibration.artifact_path",
        lambda backend: tmp_path / f"calibration_{backend}.joblib",
    )
    assert get_calibrator("keyword") is None


def test_get_calibrator_loads_and_caches(monkeypatch, tmp_path):
    reset_calibrator_cache()
    raw, labels = _miscalibrated_dataset()
    path = tmp_path / "calibration_keyword.joblib"
    ConfidenceCalibrator(method="platt").fit(raw, labels).save(path)

    monkeypatch.setattr(
        "app.pipeline.calibration.artifact_path", lambda backend: path
    )
    cal = get_calibrator("keyword")
    assert cal is not None and cal.is_fitted
    # Second call returns the cached instance.
    assert get_calibrator("keyword") is cal
    reset_calibrator_cache()


# ---------------------------------------------------------------------------
# Part 2 — pipeline wiring (flagged, off by default)
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_calibration_state():
    """Each test starts from a clean cache / warn-set."""
    reset_calibrator_cache()
    classifier_module._calibration_warned.clear()
    yield
    reset_calibrator_cache()
    classifier_module._calibration_warned.clear()


async def test_disabled_is_true_noop(monkeypatch):
    """CALIBRATION_ENABLED=False → identical to pre-5B: no calibrated fields."""
    monkeypatch.setattr(settings, "CALIBRATION_ENABLED", False)
    result = await IntentClassifier(strategy="keyword").classify(_FAQ_BODY, _FAQ_SUBJECT)
    baseline = keyword_classify(_FAQ_SUBJECT, _FAQ_BODY)

    assert result.calibrated_confidence is None
    assert result.raw_confidence is None
    # Intent + raw confidence unchanged from the pre-calibration code path.
    assert result.intent == baseline.intent
    assert result.confidence == baseline.confidence


async def test_missing_artifact_falls_back_without_raising(monkeypatch):
    """Enabled but no fitted artifact → warn once, fall back to raw (no raise)."""
    monkeypatch.setattr(settings, "CALIBRATION_ENABLED", True)
    monkeypatch.setattr(calibration_module, "get_calibrator", lambda backend: None)

    result = await IntentClassifier(strategy="keyword").classify(_FAQ_BODY, _FAQ_SUBJECT)
    assert result.calibrated_confidence is None
    assert result.confidence == keyword_classify(_FAQ_SUBJECT, _FAQ_BODY).confidence


async def test_enabled_with_artifact_sets_calibrated(monkeypatch, tmp_path):
    """Enabled + fitted artifact → raw preserved, calibrated populated."""
    # Fit a calibrator that pushes low raw scores well above the gate.
    raw, labels = _miscalibrated_dataset()
    path = tmp_path / "calibration_keyword.joblib"
    ConfidenceCalibrator(method="platt").fit(raw, labels).save(path)
    monkeypatch.setattr(calibration_module, "artifact_path", lambda backend: path)
    monkeypatch.setattr(settings, "CALIBRATION_ENABLED", True)

    result = await IntentClassifier(strategy="keyword").classify(_FAQ_BODY, _FAQ_SUBJECT)
    assert result.raw_confidence == result.confidence
    assert result.calibrated_confidence is not None
    assert 0.0 <= result.calibrated_confidence <= 1.0


def test_router_prefers_calibrated_confidence():
    """Router acts on the calibrated value (not the raw score) when present.

    The observable signal is ``confidence_used``: with a calibrated value the
    router considers 0.90; without one it falls back to the raw 0.40.
    ``submission_requirements`` is in the KB-coverage-derived
    FAQ_ELIGIBLE_INTENTS (Task B4), so the calibrated case (0.90 >= the 0.65
    gate, with grounding chunks) now genuinely reaches the "faq" lane, while
    the raw-only case (0.40, below the gate) still lands in human_review.
    """
    router = EmailRouter(strategy="rule_based")
    chunks = [object()]  # router only reads len(retrieved_chunks)

    # Raw 0.40 (below the 0.65 gate) but calibrated 0.90 → the router uses 0.90.
    calibrated = ClassificationResult(
        intent="submission_requirements",
        confidence=0.40,
        raw_confidence=0.40,
        calibrated_confidence=0.90,
    )
    decision = router.route(calibrated, chunks)
    assert decision.confidence_used == 0.90  # calibrated value drove the decision
    assert decision.lane == "faq"  # eligible + confident + grounded

    # Same raw score, no calibrated value → falls back to raw 0.40.
    raw_only = ClassificationResult(intent="submission_requirements", confidence=0.40)
    raw_decision = router.route(raw_only, chunks)
    assert raw_decision.confidence_used == 0.40
    assert raw_decision.lane == "human_review"
