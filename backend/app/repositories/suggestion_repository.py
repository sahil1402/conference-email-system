"""Persistence for CEL policy suggestions (repositories-only DB access)."""
from difflib import SequenceMatcher
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import PolicySuggestion

def _norm(title: str, content: str) -> str:
    return f"{title}\n{content}".strip().lower()

class SuggestionRepository:
    async def create(self, db, *, source_email_id, experience_summary, title, content,
                     category, intents, generalizable, reason, confidence, conflict_report):
        row = PolicySuggestion(
            source_email_id=source_email_id, experience_summary=experience_summary,
            title=title, content=content, category=category, intents=intents,
            generalizable=generalizable, reason=reason, confidence=confidence,
            conflict_report=conflict_report,
        )
        db.add(row); await db.commit(); await db.refresh(row); return row

    async def list(self, db, *, status=None, limit=200, offset=0):
        q = select(PolicySuggestion)
        if status is not None:
            q = q.where(PolicySuggestion.status == status)
        q = q.order_by(PolicySuggestion.id.desc()).limit(limit).offset(offset)
        return list((await db.execute(q)).scalars().all())

    async def get(self, db, suggestion_id):
        return (await db.execute(select(PolicySuggestion).where(PolicySuggestion.id == suggestion_id))).scalar_one_or_none()

    async def count_pending(self, db):
        return (await db.execute(select(func.count()).select_from(PolicySuggestion).where(PolicySuggestion.status == "pending"))).scalar_one()

    async def reject(self, db, suggestion_id, *, actor, reason=None):
        row = await self.get(db, suggestion_id)
        if row is None:
            return None
        row.status = "rejected"; row.reviewed_by = actor; row.reviewed_reason = reason
        await db.commit(); await db.refresh(row); return row

    async def mark_accepted(self, db, suggestion_id, *, actor, policy_key):
        row = await self.get(db, suggestion_id)
        if row is None:
            return None
        row.status = "accepted"; row.reviewed_by = actor; row.resulting_policy_key = policy_key
        await db.commit(); await db.refresh(row); return row

    async def bump_seen(self, db, suggestion_id):
        row = await self.get(db, suggestion_id)
        if row is not None:
            row.seen_count = (row.seen_count or 1) + 1
            await db.commit()

    async def find_similar(self, db, *, title, content, threshold=0.82):
        target = _norm(title, content)
        q = select(PolicySuggestion).where(PolicySuggestion.status.in_(("pending", "rejected")))
        for row in (await db.execute(q)).scalars().all():
            if SequenceMatcher(None, target, _norm(row.title, row.content)).ratio() >= threshold:
                return row
        return None
