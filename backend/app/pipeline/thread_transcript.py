"""Thread transcript builder for multi-turn classification/drafting.

Turns a ticket's stored comment thread into a compact, budget-bounded
transcript for the distiller/drafter. Internal notes (``public`` False) are
excluded — they are staff-only and must never reach the model (design D6). The
latest requester (end-user) message is surfaced separately as the
classification/retrieval anchor (design D2). Bounded recent-turns-first
(design D5): the newest turns are kept whole up to ``char_budget``; older turns
are dropped with an explicit marker, and the latest turn is never dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import MessageAuthorRole

_OMISSION_MARKER = "[… {n} earlier message(s) omitted …]"


@dataclass
class ThreadTranscript:
    """Rendered transcript + the latest requester turn (the anchor)."""

    text: str = ""
    latest_requester_message: str = ""
    included: int = 0
    omitted: int = 0


def _is_requester(msg: dict, requester_id) -> bool:
    """The ticket requester, identified by author_id — roles are unreliable
    (a requester can carry role 'agent'; chairs often carry 'end-user', the same
    reason the thread view labels by requester_id). Falls back to role only when
    the requester id is unknown (e.g. a non-Zendesk thread)."""
    if requester_id is not None:
        return msg.get("author_id") == requester_id
    return msg.get("author_role") == MessageAuthorRole.END_USER.value


def _label(msg: dict, requester_id) -> str:
    return "Requester" if _is_requester(msg, requester_id) else "Support"


def _sort_dt(dt):
    """tz-agnostic sort proxy: SQLite reads ``created_at`` back naive while
    Zendesk supplies aware datetimes, so a mixed thread would raise if compared
    directly. Strip tzinfo for ordering (all values are effectively UTC)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _body(msg: dict) -> str:
    return (msg.get("plain_body") or "").strip()


def build_transcript(
    messages: list[dict], *, char_budget: int, requester_id=None
) -> ThreadTranscript:
    """Render an AI-visible, budget-bounded transcript, latest turn anchored.

    ``requester_id`` identifies the requester by author_id for labeling + the
    anchor (roles are unreliable); omit it to fall back to role.
    """
    # AI-visible turns only: public messages with non-empty text. Internal
    # notes (public False) are excluded entirely (D6).
    visible = [m for m in messages if m.get("public") and _body(m)]
    # Defensive ordering (repo already returns oldest-first); None sorts last.
    visible.sort(key=lambda m: (m.get("created_at") is None, _sort_dt(m.get("created_at"))))
    if not visible:
        return ThreadTranscript()

    latest_requester = ""
    for m in visible:
        if _is_requester(m, requester_id):
            latest_requester = _body(m)

    # Render newest-first until the budget is spent, then reverse to reading
    # order. The newest turn is always kept (truncated only if it alone blows
    # the budget) so the current ask is never lost.
    rendered: list[str] = []
    used = 0
    included = 0
    for m in reversed(visible):
        block = f"{_label(m, requester_id)}: {_body(m)}"
        cost = len(block) + 2  # +2 for the joining blank line
        if included == 0 and cost > char_budget:
            rendered.append(block[:char_budget])
            included = 1
            break
        if used + cost > char_budget:
            break
        rendered.append(block)
        used += cost
        included += 1

    omitted = len(visible) - included
    rendered.reverse()
    parts = ([_OMISSION_MARKER.format(n=omitted)] + rendered) if omitted > 0 else rendered
    return ThreadTranscript(
        text="\n\n".join(parts),
        latest_requester_message=latest_requester,
        included=included,
        omitted=omitted,
    )
