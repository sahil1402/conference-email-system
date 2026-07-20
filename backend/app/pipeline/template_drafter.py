"""Template drafter (zero-AI, fully offline reply generation).

The safest possible drafter backend: it makes **no model call at all**. Instead
it fills a fixed response template directly from the retrieved policy chunks —
a hand-written per-intent opening, the retrieved policy text *verbatim* (the
actual, grounded answer — never paraphrased or generated), and a standard
closing. This is the fallback for conferences that forbid AI-generated content,
or when both the cloud and self-hosted backends are unavailable.

Grounding guarantee: with zero retrieved chunks the drafter refuses to fabricate
an answer and instead returns a clear "routed to a human" message. Because the
body is copied verbatim from policy text, hallucination risk is zero by
construction.

Returns the same ``DraftResponse`` shape as ``ResponseDrafter`` so it is a true
drop-in behind ``MODEL_PROVIDER=template``. ``DraftResponse`` is imported from
drafter.py; drafter.py imports this module lazily (inside its dispatch) to avoid
an import cycle.
"""

from app.pipeline.drafter import DraftResponse

# One hand-written opening line per intent (VALID_INTENTS, taxonomy.py). Kept
# deliberately short and neutral; the substantive content is the verbatim
# policy text below.
_OPENINGS: dict[str, str] = {
    "reviewer_assignment": "Thank you for contacting us about reviewer assignments.",
    "review_submission_help": "Thank you for your message about submitting a review.",
    "paper_bidding": "Thank you for your question about paper bidding.",
    "author_profile_compliance": "Thank you for your question about your author profile.",
    "submission_upload_help": "Thank you for your message about uploading your submission.",
    "submission_requirements": "Thank you for your question about submission requirements.",
    "submission_format_policy": "Thank you for your question about the formatting requirements.",
    "author_list_change": "Thank you for your request to update the author list.",
    "review_decision_appeal": "Thank you for raising your concern about the review decision.",
    "desk_reject_appeal": "Thank you for your appeal regarding the desk rejection.",
    "anonymity_violation": "Thank you for bringing this anonymity concern to our attention.",
    "reviewer_workload_role": "Thank you for your message about reviewer workload or role.",
    "committee_invitation": "Thank you for your response to the committee invitation.",
    "cms_support": "Thank you for contacting the program committee.",
}

_DEFAULT_OPENING = "Thank you for contacting the program committee."

_CLOSING = (
    "If this does not fully resolve your question, please reply to this email and "
    "a program chair will follow up.\n\n— Conference Program Committee"
)

# Returned when nothing was retrieved — we never fabricate an ungrounded answer.
_NO_GROUNDING_MESSAGE = (
    "Thank you for your message. We could not find a specific policy that answers "
    "your question automatically, so it has been routed to a program chair who will "
    "reply to you directly.\n\n— Conference Program Committee"
)


class TemplateDrafter:
    """Fills a response template from retrieved policy chunks — no model call."""

    def draft(
        self, email: dict, intent: str, retrieved_chunks: list
    ) -> DraftResponse:
        """Build a grounded reply from templates + verbatim policy text.

        ``email`` is accepted for signature parity with the AI drafters (the
        template does not need the raw email body). With no retrieved chunks it
        returns a human-review message rather than a hollow template.
        """
        if not retrieved_chunks:
            return DraftResponse(
                draft_text=_NO_GROUNDING_MESSAGE,
                citations=[],
                model_used="template",
                generation_metadata={
                    "provider": "template",
                    "grounded": False,
                    "reason": "no_policy_chunks",
                },
            )

        opening = _OPENINGS.get(intent, _DEFAULT_OPENING)

        # Verbatim policy text, one paragraph per chunk. Ids are internal
        # indexing and never appear in requester-facing text — provenance lives
        # only in the citations list.
        policy_blocks = [chunk.content for chunk in retrieved_chunks]
        citations = [chunk.policy_id for chunk in retrieved_chunks]

        draft_text = "\n\n".join([opening, *policy_blocks, _CLOSING])

        return DraftResponse(
            draft_text=draft_text,
            citations=citations,
            model_used="template",
            generation_metadata={
                "provider": "template",
                "grounded": True,
                "chunk_count": len(retrieved_chunks),
                "intent": intent,
            },
        )
