"""Policy / FAQ knowledge-base persistence (the PolicyDocument aggregate).

Backs the retriever's grounding corpus. All access to the ``policy_documents``
table goes through this repository. Reads return ``[]`` on miss; the bulk insert
commits once and returns the number of rows written.
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PolicyDocument

# Columns on PolicyDocument that a caller-supplied dict may populate. Incoming
# dicts (e.g. raw policies.json with id/source/tags) are filtered to these, and
# the knowledge-base "id" is accepted as an alias for the unique ``policy_key``.
_POLICY_COLUMNS = {
    "policy_key", "title", "content", "category", "score", "tags", "source",
    "visibility", "status",
}


def _map_policy(raw: dict) -> dict:
    """Project an arbitrary policy dict onto valid ``PolicyDocument`` columns.

    Accepts ``id`` as an alias for ``policy_key`` so the project's
    ``policies.json`` (which uses ``id``) can be loaded directly. As of Phase E,
    ``tags`` and ``source`` are real columns and pass through (previously they
    were dropped) — this is what gives the DB-backed FAISS retriever tag parity
    with BM25. Any other unrecognised key is still dropped.
    """
    mapped = {k: v for k, v in raw.items() if k in _POLICY_COLUMNS}
    if "policy_key" not in mapped and "id" in raw:
        mapped["policy_key"] = raw["id"]
    return mapped


class PolicyRepository:
    """Async data-access methods for the ``policy_documents`` table."""

    # Content fields the importer owns. status/visibility are chair-owned and are
    # never written on update (prevents a re-scrape resurrecting a retired policy).
    _IMPORTER_FIELDS = ("title", "content", "category", "tags")

    async def get_all_policies(self, db: AsyncSession) -> list[PolicyDocument]:
        """Return every policy document, ordered by id."""
        result = await db.execute(select(PolicyDocument).order_by(PolicyDocument.id))
        return list(result.scalars().all())

    async def get_policies_by_category(
        self, db: AsyncSession, category: str
    ) -> list[PolicyDocument]:
        """Return policy documents in a given category, ordered by id."""
        result = await db.execute(
            select(PolicyDocument)
            .where(PolicyDocument.category == category)
            .order_by(PolicyDocument.id)
        )
        return list(result.scalars().all())

    async def bulk_insert_policies(
        self, db: AsyncSession, policies: list[dict]
    ) -> int:
        """Insert many policy documents in one transaction.

        Each dict is projected onto valid columns (``id`` aliases
        ``policy_key``). Returns the number of rows inserted; an empty input
        inserts nothing and returns 0.
        """
        if not policies:
            return 0
        rows = [PolicyDocument(**_map_policy(p)) for p in policies]
        db.add_all(rows)
        await db.commit()
        return len(rows)

    async def list_for_index(
        self,
        db: AsyncSession,
        visibilities: tuple[str, ...] = ("public", "internal"),
    ) -> list[PolicyDocument]:
        """Return active policies whose visibility is in ``visibilities``.

        This is the single corpus query every retriever indexes, so the
        visibility/status filter lives in exactly one place.
        """
        result = await db.execute(
            select(PolicyDocument)
            .where(PolicyDocument.status == "active")
            .where(PolicyDocument.visibility.in_(visibilities))
            .order_by(PolicyDocument.id)
        )
        return list(result.scalars().all())

    async def upsert_by_key(self, db: AsyncSession, raw: dict, *, source: str) -> str:
        """Insert a new public policy or refresh an existing one's content.

        Returns "inserted" or "updated". On update, only content fields change;
        status/visibility are left as-is.
        """
        mapped = _map_policy(raw)
        key = mapped.get("policy_key")
        if not key:
            raise ValueError("policy dict needs 'policy_key' or 'id'")

        existing = (
            await db.execute(select(PolicyDocument).where(PolicyDocument.policy_key == key))
        ).scalar_one_or_none()

        if existing is None:
            content = {k: v for k, v in mapped.items() if k not in ("source", "visibility", "status")}
            db.add(PolicyDocument(visibility="public", status="active", source=source, **content))
            await db.commit()
            return "inserted"

        for field in self._IMPORTER_FIELDS:
            if field in mapped:
                setattr(existing, field, mapped[field])
        existing.source = source
        await db.commit()
        return "updated"

    @staticmethod
    def _slugify(text: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        return slug or "policy"

    async def get_by_key(self, db: AsyncSession, policy_key: str) -> PolicyDocument | None:
        return (
            await db.execute(select(PolicyDocument).where(PolicyDocument.policy_key == policy_key))
        ).scalar_one_or_none()

    async def create_internal(
        self,
        db: AsyncSession,
        *,
        title: str,
        content: str,
        category: str | None = None,
        tags: list | None = None,
        actor: str,
    ) -> PolicyDocument:
        """Insert a chair-authored internal policy with a generated unique key."""
        base = f"int_{self._slugify(title)}"
        key, n = base, 1
        while await self.get_by_key(db, key) is not None:
            n += 1
            key = f"{base}-{n}"
        row = PolicyDocument(
            policy_key=key, title=title, content=content, category=category,
            tags=tags or [], source=f"chair:{actor}", visibility="internal", status="active",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    async def retire(self, db: AsyncSession, policy_key: str) -> PolicyDocument | None:
        """Soft-retire a policy (status='inactive'). Returns the row or None."""
        row = await self.get_by_key(db, policy_key)
        if row is None:
            return None
        row.status = "inactive"
        await db.commit()
        await db.refresh(row)
        return row
