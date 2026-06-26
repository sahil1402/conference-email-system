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

from app.db.database import get_db
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
