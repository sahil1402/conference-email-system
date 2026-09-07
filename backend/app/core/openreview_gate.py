"""OpenReview post gate — the single precondition for relaying a reply onward.

Sibling to :mod:`app.core.send_gate`, deliberately NOT a reuse of it. That
module's shape is copied exactly — pure, synchronous, no I/O, returns a decision
object that the transport must honour, and both outcomes get audited — because
it was built to be the one blessed entry condition for a send. Its POLICY,
however, does not transfer, and reusing it here would have been actively wrong:

* ``authorize_send`` reads ``email.draft["draft_text"]`` and refuses on
  ``[CHAIR: ...]`` placeholders. That text is the AI's drafted EMAIL REPLY to the
  requester. What this gate governs is a different string entirely — the
  requester's own words, quote-stripped, on their way to OpenReview. Gating one
  on the other means refusing a perfectly good relay because an unrelated draft
  is unfinished, or allowing one because an unrelated draft happens to be clean.
* Its ``ALLOW_AUTO_SEND`` branch authorises unreviewed FAQ-lane drafts to go out
  automatically. There is no equivalent here and there must not be: nothing
  auto-posts to a public venue, so this gate has no policy flag at all.

WHAT THIS GATE DOES NOT DO
--------------------------
It does not check the email's workflow ``status``. The OpenReview relay is a
separate decision from approving the AI's email draft, and requiring
``status == "approved"`` would force a chair to perform a semantically unrelated
action first. The human act being relied on is the explicit, argument-bearing
call to the endpoint itself. ⚠️ That is a policy choice worth a second opinion —
if the relay should also require a prior draft approval, this is the one place to
add it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OpenReviewPostDecision(BaseModel):
    """Outcome of the gate — the relay proceeds ONLY when authorized."""

    authorized: bool
    reason: str = Field(..., description="Human-readable rationale, audited.")
    note_id: str | None = Field(
        default=None,
        description="The parent note the reply threads under. None when refused.",
    )
    forum_id: str | None = Field(
        default=None,
        description="The forum the parent note belongs to. None when refused.",
    )


def _first_forum_id(extraction: dict[str, Any]) -> str | None:
    """The forum this reply belongs to.

    ``openreview_forum_ids`` is a LIST because an email may name several papers,
    but a note id is a scalar and — per the coherence gate in the extractor — can
    only exist alongside a forum this same result reports. The first entry is
    therefore the forum the note came from. The endpoint re-verifies this against
    the parent note's OWN forum before posting, so a wrong guess here is caught
    rather than acted on.
    """
    for value in extraction.get("openreview_forum_ids") or []:
        cleaned = (value or "").strip()
        if cleaned:
            return cleaned
    return None


def authorize_openreview_post(email, reply_text: str) -> OpenReviewPostDecision:
    """Decide whether ``email``'s reply may be relayed to OpenReview.

    Pure and synchronous: reads the email row and the caller's final text, no
    I/O. Every relay path MUST call this and honour the result.

    ``reply_text`` is passed IN rather than read off the email because the chair
    may have edited it in the review UI — the text that gets gated has to be the
    text that gets posted, or the gate is checking something else.
    """
    extraction = dict(getattr(email, "extraction", None) or {})

    if not extraction:
        return OpenReviewPostDecision(
            authorized=False,
            reason="This email has no extraction record, so it was never "
            "examined for an OpenReview reply.",
        )

    # The narrow purpose of this endpoint. This is NOT a general "post anything
    # to OpenReview" path: only an email detected as a reply to an OpenReview
    # notification may be relayed, because only there do we know the parent
    # comment and its audience.
    if not extraction.get("openreview_reply_candidate"):
        return OpenReviewPostDecision(
            authorized=False,
            reason="This email is not an OpenReview reply candidate "
            "(openreview_reply_candidate is false), so there is no parent "
            "comment to reply to.",
        )

    note_id = (extraction.get("openreview_note_id") or "").strip()
    if not note_id:
        return OpenReviewPostDecision(
            authorized=False,
            reason="No OpenReview note id was extracted, so there is no "
            "specific comment to thread this reply under.",
        )

    forum_id = _first_forum_id(extraction)
    if not forum_id:
        return OpenReviewPostDecision(
            authorized=False,
            reason="No OpenReview forum id was extracted, so the reply cannot "
            "be placed in a submission's discussion.",
        )

    if not (reply_text or "").strip():
        return OpenReviewPostDecision(
            authorized=False,
            reason="The reply text is empty; there is nothing to post.",
        )

    # Idempotency. Posting the same comment twice is publicly visible and cannot
    # be undone from here, and a double click or a client retry is the ordinary
    # way it would happen. A recorded successful post is therefore a hard stop.
    already = dict(getattr(email, "draft", None) or {}).get("openreview_post") or {}
    if already.get("state") == "posted":
        return OpenReviewPostDecision(
            authorized=False,
            reason="This reply has already been posted to OpenReview "
            f"(note {already.get('note_id') or 'unknown'}). Refusing to post a "
            "duplicate.",
        )

    return OpenReviewPostDecision(
        authorized=True,
        reason="OpenReview reply candidate with a parent note and non-empty text.",
        note_id=note_id,
        forum_id=forum_id,
    )
