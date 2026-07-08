"""Analytics API (v1) — dashboard summary and recent activity.

Aggregates are computed in Python over the rows returned by the repositories so
no new repository methods or raw SQL are introduced here. Lane and confidence
are read from the Email JSON columns (`routing.lane`, `classification.confidence`
/ `classification.intent`), which is where the pipeline persists them.
"""

from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import get_db
from app.pipeline.calibration import (
    backend_key,
    brier_score,
    collect_calibration_pairs,
    expected_calibration_error,
    get_calibrator,
    reliability_table,
)
from app.pipeline.rl_router import get_rl_router
from app.repositories.audit_repository import AuditRepository
from app.repositories.email_repository import EmailRepository

router = APIRouter(prefix="/analytics", tags=["analytics"])

email_repo = EmailRepository()
audit_repo = AuditRepository()

# Statuses that count as a resolved chair decision (everything else is pending).
_RESOLVED_STATUSES = {"approved", "rerouted"}
# A large page size to pull the whole (small, MVP-scale) table for aggregation.
_ALL = 1_000_000
_DAILY_WINDOW_DAYS = 7


@router.get("/summary")
async def analytics_summary(db: AsyncSession = Depends(get_db)) -> dict:
    """Return dashboard metrics aggregated across all emails."""
    emails = await email_repo.get_email_queue(db, lane=None, limit=_ALL, offset=0)

    total = len(emails)
    faq_count = 0
    human_review_count = 0
    approved_count = 0
    pending_count = 0
    confidences: list[float] = []
    intent_counter: Counter[str] = Counter()

    # Build the last-7-days date buckets (oldest → newest), all starting at 0.
    today = date.today()
    daily_buckets: dict[str, int] = {
        (today - timedelta(days=offset)).isoformat(): 0
        for offset in range(_DAILY_WINDOW_DAYS - 1, -1, -1)
    }

    for email in emails:
        routing = email.routing or {}
        classification = email.classification or {}

        lane = routing.get("lane")
        if lane == "faq":
            faq_count += 1
        elif lane == "human_review":
            human_review_count += 1

        if email.status == "approved":
            approved_count += 1
        if email.status not in _RESOLVED_STATUSES:
            pending_count += 1

        confidence = classification.get("confidence")
        if isinstance(confidence, (int, float)):
            confidences.append(float(confidence))

        intent = classification.get("intent")
        if intent:
            intent_counter[intent] += 1

        if email.received_at:
            key = email.received_at.date().isoformat()
            if key in daily_buckets:
                daily_buckets[key] += 1

    avg_confidence = (
        round(sum(confidences) / len(confidences), 4) if confidences else 0.0
    )

    return {
        "total_emails": total,
        "faq_lane_count": faq_count,
        "human_review_count": human_review_count,
        "approved_count": approved_count,
        "pending_count": pending_count,
        "avg_confidence": avg_confidence,
        "intent_distribution": dict(intent_counter),
        "daily_volume": [
            {"date": day, "count": count} for day, count in daily_buckets.items()
        ],
    }


@router.get("/rl-stats")
async def rl_stats() -> dict:
    """Return the RL router's per-intent win rates (empty unless RL is active)."""
    if settings.ROUTING_STRATEGY != "rl":
        return {"routing_strategy": "rule_based", "stats": {}}
    return {"routing_strategy": "rl", "stats": get_rl_router().get_stats()}


# Visible caveat that must reach the UI (not just a tooltip): these numbers are
# computed on the same 58-email set the calibrator was fit on (Phase 5B).
_CALIBRATION_CAVEAT = (
    "Based on the 58-email evaluation set. The calibrator was fit on this same "
    "set, so these numbers are an in-sample upper bound, not held-out performance."
)


def _reliability_rows(scores: list[float], labels: list[int]) -> list[dict]:
    """Reliability-table rows shaped for the chart: bucket, n, mean_confidence,
    accuracy, and the signed gap (accuracy − mean_confidence)."""
    return [
        {
            "bucket": row["bucket"],
            "n": row["count"],
            "mean_confidence": row["mean_confidence"],
            "accuracy": row["accuracy"],
            "gap": round(row["accuracy"] - row["mean_confidence"], 4),
        }
        for row in reliability_table(scores, labels)
    ]


@router.get("/calibration")
async def calibration_report() -> dict:
    """Reliability data for the calibration diagram (raw, plus calibrated if fit).

    Runs the active classifier over the ground-truth eval set to get
    (raw_confidence, was_correct) pairs, then returns decile reliability tables.
    When a fitted calibrator artifact exists for the backend it also returns the
    calibrated series; otherwise ``calibrated_available`` is false and the
    calibrated fields are null (no error).
    """
    backend = settings.CLASSIFIER_BACKEND
    key = backend_key(backend)

    raw_scores, labels, _ = await collect_calibration_pairs(backend)

    metrics = {
        "brier_raw": round(brier_score(raw_scores, labels), 4),
        "ece_raw": round(expected_calibration_error(raw_scores, labels), 4),
    }

    calibrator = get_calibrator(key)
    calibrated_rows = None
    if calibrator is not None:
        calibrated_scores = [calibrator.calibrate(s) for s in raw_scores]
        calibrated_rows = _reliability_rows(calibrated_scores, labels)
        metrics["brier_calibrated"] = round(brier_score(calibrated_scores, labels), 4)
        metrics["ece_calibrated"] = round(
            expected_calibration_error(calibrated_scores, labels), 4
        )

    return {
        "backend": key,
        "eval_set_size": len(raw_scores),
        "calibration_enabled": settings.CALIBRATION_ENABLED,
        "calibrated_available": calibrator is not None,
        "raw": _reliability_rows(raw_scores, labels),
        "calibrated": calibrated_rows,
        "metrics": metrics,
        "caveat": _CALIBRATION_CAVEAT,
    }


@router.get("/recent-activity")
async def recent_activity(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Return the 20 most recent audit-trail entries."""
    entries = await audit_repo.get_recent_actions(db, limit=20)
    return [
        {
            "email_id": str(entry.email_id),
            "action": entry.action,
            "actor": entry.actor,
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
        }
        for entry in entries
    ]
