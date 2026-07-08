"""Tests for GET /api/v1/analytics/calibration (Phase 5E, Part 2).

Verifies the reliability-diagram endpoint returns the expected structure both
without a fitted calibrator (raw-only, flagged) and with one (raw + calibrated).
No DB is needed — the endpoint reads the ground-truth file and runs the keyword
classifier. The calibration artifact lookup is redirected to a temp path so the
two cases are deterministic regardless of what is on disk.
"""

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

import main
import app.pipeline.calibration as calibration_module
from app.pipeline.calibration import ConfidenceCalibrator, reset_calibrator_cache

_REQUIRED_ROW_KEYS = {"bucket", "n", "mean_confidence", "accuracy", "gap"}


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_calibration_endpoint_raw_only_when_no_artifact(client, monkeypatch, tmp_path):
    reset_calibrator_cache()
    # Point the artifact lookup at an empty dir → no calibrator fitted yet.
    monkeypatch.setattr(
        calibration_module,
        "artifact_path",
        lambda backend: tmp_path / f"calibration_{backend}.joblib",
    )

    resp = await client.get("/api/v1/analytics/calibration")
    assert resp.status_code == 200
    body = resp.json()

    assert body["calibrated_available"] is False
    assert body["calibrated"] is None
    assert body["eval_set_size"] > 0
    assert body["raw"] and all(_REQUIRED_ROW_KEYS <= set(r) for r in body["raw"])
    # Raw metrics present; calibrated metrics absent.
    assert "brier_raw" in body["metrics"]
    assert "brier_calibrated" not in body["metrics"]
    # In-sample caveat is carried in the payload (the UI surfaces it visibly).
    assert "in-sample" in body["caveat"].lower()
    reset_calibrator_cache()


async def test_calibration_endpoint_includes_calibrated_when_artifact_exists(
    client, monkeypatch, tmp_path
):
    reset_calibrator_cache()
    # Fit + save a real calibrator, then redirect the lookup to it.
    path = tmp_path / "calibration_keyword.joblib"
    raw = [0.1, 0.3, 0.5, 0.7, 0.9, 0.2, 0.4, 0.6, 0.8, 0.5]
    labels = [0, 0, 1, 1, 1, 0, 1, 1, 1, 0]
    ConfidenceCalibrator(method="platt").fit(raw, labels).save(path)
    monkeypatch.setattr(calibration_module, "artifact_path", lambda backend: path)

    resp = await client.get("/api/v1/analytics/calibration")
    assert resp.status_code == 200
    body = resp.json()

    assert body["calibrated_available"] is True
    assert body["calibrated"] and all(
        _REQUIRED_ROW_KEYS <= set(r) for r in body["calibrated"]
    )
    assert "brier_calibrated" in body["metrics"]
    assert "ece_calibrated" in body["metrics"]
    reset_calibrator_cache()


async def test_calibration_rows_are_internally_consistent(client, monkeypatch, tmp_path):
    reset_calibrator_cache()
    monkeypatch.setattr(
        calibration_module,
        "artifact_path",
        lambda backend: tmp_path / f"calibration_{backend}.joblib",
    )
    body = (await client.get("/api/v1/analytics/calibration")).json()
    for row in body["raw"]:
        assert 0.0 <= row["mean_confidence"] <= 1.0
        assert 0.0 <= row["accuracy"] <= 1.0
        assert row["n"] >= 1
        assert row["gap"] == pytest.approx(row["accuracy"] - row["mean_confidence"], abs=1e-4)
    reset_calibrator_cache()
