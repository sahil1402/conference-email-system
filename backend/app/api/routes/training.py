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
