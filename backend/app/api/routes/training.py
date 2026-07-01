"""Training routes — fit the trainable classifier on labeled email data.

Mounted under ``/api/v1`` by main.py, so the public path is
``/api/v1/train/classifier``. Training is CPU-bound (embedding + sklearn fit),
so it is run in a threadpool to avoid blocking the event loop. The shared
TrainableClassifier singleton is updated in place, so the next classification
request uses the freshly trained model with no restart.
"""

import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.pipeline.calibration import VALID_METHODS, fit_calibrator_for_backend
from app.pipeline.trainable_classifier import (
    TrainableClassifier,
    get_trainable_classifier,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/train", tags=["training"])

# Minimum labeled samples required to attempt training.
_MIN_SAMPLES = 5


class TrainingSample(BaseModel):
    """One labeled training example."""

    subject: str
    body: str
    intent: str


class TrainClassifierRequest(BaseModel):
    """Payload for a classifier training run."""

    # min_length=5 → FastAPI returns 422 automatically for fewer samples.
    training_data: list[TrainingSample] = Field(..., min_length=_MIN_SAMPLES)


@router.post("/classifier")
async def train_classifier(payload: TrainClassifierRequest) -> dict:
    """Train the intent classifier on labeled data and persist the model."""
    samples = [s.model_dump() for s in payload.training_data]

    try:
        classifier = get_trainable_classifier()
        result = await run_in_threadpool(classifier.train, samples)
    except Exception as exc:  # noqa: BLE001 - surface training failure as 500
        logger.exception("Classifier training failed.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Training failed: {exc}",
        ) from exc

    return {
        "status": "trained",
        "samples": result["samples"],
        "classes": result["classes"],
        "accuracy": result["accuracy"],
        "model_path": TrainableClassifier.MODEL_PATH,
    }


class TrainCalibrationRequest(BaseModel):
    """Payload for a calibration fitting run (Phase 5B).

    Fits against the labeled ground-truth set on disk, so no samples are sent in
    the body — only the backend to calibrate and the method to use.
    """

    backend: str = Field(default="keyword", pattern="^(keyword|trainable)$")
    method: str = Field(default="platt")


@router.post("/calibration")
async def train_calibration(payload: TrainCalibrationRequest | None = None) -> dict:
    """Fit + persist a confidence calibrator for a classifier backend.

    Runs the classifier over the ground-truth set, fits the calibrator, and saves
    the artifact under backend/models/ (CPU-bound → threadpool). Mirrors
    /train/classifier. The calibrator only takes effect when CALIBRATION_ENABLED.
    """
    req = payload or TrainCalibrationRequest()
    if req.method not in VALID_METHODS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown method '{req.method}'; expected one of {list(VALID_METHODS)}.",
        )

    try:
        report = await run_in_threadpool(
            fit_calibrator_for_backend, req.backend, req.method
        )
    except Exception as exc:  # noqa: BLE001 - surface fitting failure as 500
        logger.exception("Calibration fitting failed.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Calibration fitting failed: {exc}",
        ) from exc

    return {
        "status": "calibrated",
        "backend": report["backend"],
        "method": report["method"],
        "samples": report["samples"],
        "brier_before": report["brier_before"],
        "brier_after": report["brier_after"],
        "ece_before": report["ece_before"],
        "ece_after": report["ece_after"],
        "artifact_path": report["artifact_path"],
    }
