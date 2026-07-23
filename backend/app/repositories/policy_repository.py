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
    # [tags-dropped E007] "tags" removed — column dropped, no retrieval signal.
    "policy_key", "title", "content", "category", "score", "source",
    "visibility", "status", "intents",
    "supersedes", "superseded_by", "root_key", "version",
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
    # [tags-dropped E007] "tags" removed from the importer fields.
    _IMPORTER_FIELDS = ("title", "content", "category", "intents")

    async def get_all_policies(self, db: AsyncSession) -> list[PolicyDocument]:
        """Return every policy document, ordered by id."""
        result = await db.execute(select(PolicyDocument).order_by(PolicyDocument.id))
        return list(result.scalars().all())

    async def get_by_key(
        self, db: AsyncSession, policy_key: str
    ) -> PolicyDocument | None:
        """Return a single policy document by its unique ``policy_key``, or None.

        Backs the read-only citation-detail lookup (``GET /api/v1/policies/{key}``).
        ``policy_key`` is the knowledge-base id (e.g. ``policy_117``).
        """
        result = await db.execute(
            select(PolicyDocument).where(PolicyDocument.policy_key == policy_key)
        )
        return result.scalar_one_or_none()

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

    async def create_internal(
        self,
        db: AsyncSession,
        *,
        title: str,
        content: str,
        category: str | None = None,
        # [tags-dropped E007] tags param retained (accepts + ignores) so callers
        # need no change; the value is no longer persisted (column dropped).
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
            # [tags-dropped E007] tags=tags or [],
            source=f"chair:{actor}", visibility="internal", status="active",
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

    async def active_lineage_members(
        self, db: AsyncSession, root_key: str, *, exclude_key: str
    ) -> list[PolicyDocument]:
        """Active policies in the lineage rooted at ``root_key``, excluding ``exclude_key``.

        Membership: a row is in lineage ``L`` (``root_key``) if ``_root_of(row) ==
        L``, i.e. ``row.root_key == L`` OR (``row.root_key IS NULL`` AND
        ``row.policy_key == L``). Backs the reactivate guard: at most one member
        of a lineage should ever be active at a time.

        NOTE: placed here (above ``list``, the plain browse method) rather than
        near ``_root_of``/``edit_policy`` -- this class defines a method named
        ``list``, which shadows the builtin ``list`` name for any bare
        ``list[...]`` return-type annotation written below it in the class body
        (annotations are evaluated at class-definition time against the
        in-progress class namespace). Keep any new ``-> list[...]``-annotated
        method above ``async def list(...)``.
        """
        result = await db.execute(
            select(PolicyDocument)
            .where(PolicyDocument.status == "active")
            .where(
                (PolicyDocument.root_key == root_key)
                | (
                    PolicyDocument.root_key.is_(None)
                    & (PolicyDocument.policy_key == root_key)
                )
            )
            .where(PolicyDocument.policy_key != exclude_key)
        )
        return list(result.scalars().all())

    async def list(
        self,
        db: AsyncSession,
        *,
        visibility: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[PolicyDocument]:
        """Filtered browse of the KB (exact visibility/status; case-insensitive
        substring search over title+content), ordered by id."""
        stmt = select(PolicyDocument)
        if visibility is not None:
            stmt = stmt.where(PolicyDocument.visibility == visibility)
        if status is not None:
            stmt = stmt.where(PolicyDocument.status == status)
        if search:
            like = f"%{search}%"
            stmt = stmt.where(
                PolicyDocument.title.ilike(like) | PolicyDocument.content.ilike(like)
            )
        stmt = stmt.order_by(PolicyDocument.id).limit(limit).offset(offset)
        return list((await db.execute(stmt)).scalars().all())

    async def reactivate(self, db: AsyncSession, policy_key: str) -> PolicyDocument | None:
        """Undo a retirement: set status='active'. Returns the row or None."""
        row = await self.get_by_key(db, policy_key)
        if row is None:
            return None
        row.status = "active"
        await db.commit()
        await db.refresh(row)
        return row

    async def set_conflict_report(
        self, db: AsyncSession, policy_key: str, report: dict | None
    ) -> PolicyDocument | None:
        """Persist (or clear) a policy's conflict report (2e). Row or None.

        Assigning a fresh dict is a full reassignment, so SQLAlchemy tracks the
        change without a Mutable wrapper.
        """
        row = await self.get_by_key(db, policy_key)
        if row is None:
            return None
        row.conflict_report = report
        await db.commit()
        await db.refresh(row)
        return row

    async def active_keys(self, db: AsyncSession, keys) -> set[str]:
        """Subset of ``keys`` that are currently an active, non-superseded tip.

        Backs the conflict-report staleness prune: a conflict pointing at a now
        retired/superseded policy is moot and should not be shown.
        """
        if not keys:
            return set()
        result = await db.execute(
            select(PolicyDocument.policy_key).where(
                PolicyDocument.policy_key.in_(list(keys)),
                PolicyDocument.status == "active",
                PolicyDocument.superseded_by.is_(None),
            )
        )
        return set(result.scalars().all())

    @staticmethod
    def _root_of(policy: PolicyDocument) -> str:
        """Lineage root key: a versioned row carries ``root_key``; an original is
        its own root (``root_key`` is NULL)."""
        return policy.root_key or policy.policy_key

    async def edit_policy(
        self,
        db: AsyncSession,
        *,
        base: PolicyDocument,
        title: str,
        content: str,
        category: str | None,
        visibility: str,
        actor: str,
    ) -> PolicyDocument:
        """Create a new active version from ``base`` and retire ``base`` in place.

        The new row supersedes ``base``; ``base.superseded_by`` points forward to
        it. ``intents`` carry over unchanged. The caller validates that ``base``
        is the active tip and writes the audit entry. Commits once.
        """
        root = self._root_of(base)
        new_version = base.version + 1
        key = f"{root}__v{new_version}"
        n = 1
        while await self.get_by_key(db, key) is not None:
            n += 1
            key = f"{root}__v{new_version}-{n}"
        new_row = PolicyDocument(
            policy_key=key, title=title, content=content, category=category,
            source=f"chair:{actor}", visibility=visibility, status="active",
            intents=list(base.intents) if base.intents else None,
            supersedes=base.policy_key, superseded_by=None,
            root_key=root, version=new_version,
        )
        base.status = "inactive"
        base.superseded_by = key
        db.add(new_row)
        await db.commit()
        await db.refresh(new_row)
        return new_row

    async def revert_edit(
        self, db: AsyncSession, *, tip: PolicyDocument
    ) -> PolicyDocument:
        """Undo one edit: reactivate ``tip``'s ancestor and retire ``tip``.

        Returns the reactivated ancestor. The caller validates that ``tip`` is an
        active tip with a ``supersedes`` ancestor and writes the audit entry.
        """
        ancestor = await self.get_by_key(db, tip.supersedes)
        ancestor.status = "active"
        ancestor.superseded_by = None
        tip.status = "inactive"
        await db.commit()
        await db.refresh(ancestor)
        return ancestor
