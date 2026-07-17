"""Send gate — the load-bearing precondition for ANY outbound transport.

No transport exists yet (no SMTP, no Zendesk write-back). This module is
built FIRST so that when one lands, it has exactly one blessed entry
condition: ``authorize_send`` must return ``authorized=True`` for the email,
or nothing may be transmitted. The rule is enforced at the transport seam —
not in the UI, not by lane convention — so no future wiring mistake can
auto-send unapproved drafts.

Policy (settings.ALLOW_AUTO_SEND):
- False (default): only a chair-approved email (status "approved") may be
  sent — regardless of lane. The FAQ lane produces suggestions, not sends.
- True: a complete FAQ-lane draft (no [CHAIR: ...] placeholders, no leak
  flags, non-empty text) may additionally be sent without approval.

Independent of policy, a draft whose CURRENT text still contains
[CHAIR: ...] placeholders is never sendable — even a (mistaken) approval
does not override that; the placeholder is by definition unfinished text.
"""

from pydantic import BaseModel, Field

from app.core.config import settings
from app.pipeline.drafter import find_placeholders

# Lifecycle statuses are written with mixed casing across the codebase
# (pipeline: "DRAFT_GENERATED"; chair actions: "approved") — compare folded.
_APPROVED = "approved"
_DRAFT_GENERATED = "draft_generated"


class SendDecision(BaseModel):
    """Outcome of the gate — transports transmit ONLY when authorized."""

    authorized: bool
    mode: str | None = Field(
        default=None,
        description='How the send was authorized: "approved" (a chair signed '
        'off) or "auto" (ALLOW_AUTO_SEND policy). None when refused.',
    )
    reason: str = Field(..., description="Human-readable rationale, audited.")


def authorize_send(email) -> SendDecision:
    """Decide whether ``email``'s current draft may be transmitted.

    Pure and synchronous: reads the email row (status, routing, draft) and
    settings — no I/O. Every transport implementation MUST call this and
    honor the decision.
    """
    draft = dict(email.draft or {})
    text = (draft.get("draft_text") or "").strip()
    status = (email.status or "").lower()

    if not text:
        return SendDecision(authorized=False, reason="No draft text to send.")

    # Unfinished text never leaves the system, whatever the status or policy.
    unresolved = find_placeholders(text)
    if unresolved:
        return SendDecision(
            authorized=False,
            reason=f"Draft contains {len(unresolved)} unresolved "
            "[CHAIR: ...] placeholder(s).",
        )

    if status == _APPROVED:
        return SendDecision(
            authorized=True, mode="approved",
            reason="Chair-approved draft.",
        )

    if settings.ALLOW_AUTO_SEND:
        lane = (email.routing or {}).get("lane")
        leaks = (draft.get("generation_metadata") or {}).get("reply_leaks")
        if lane == "faq" and status == _DRAFT_GENERATED and not leaks:
            return SendDecision(
                authorized=True, mode="auto",
                reason="Complete FAQ-lane draft; ALLOW_AUTO_SEND policy.",
            )
        if lane == "faq" and leaks:
            return SendDecision(
                authorized=False,
                reason="FAQ-lane draft is leak-flagged; requires approval.",
            )

    return SendDecision(
        authorized=False,
        reason="Human approval required before sending (status is "
        f"'{email.status}', ALLOW_AUTO_SEND="
        f"{settings.ALLOW_AUTO_SEND}).",
    )
