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

# One hand-written opening line per intent (VALID_INTENTS). Kept deliberately
# short and neutral; the substantive content is the verbatim policy text below.
_OPENINGS: dict[str, str] = {
    "submission_deadline": "Thank you for your question about the submission deadline.",
    "formatting_requirements": "Thank you for your question about the formatting requirements.",
    "general_inquiry": "Thank you for reaching out to the program committee.",
    "review_assignment": "Thank you for contacting us about your review assignment.",
    "authorship_dispute": "Thank you for raising your authorship concern.",
    "submission_withdrawal": "Thank you for your message about withdrawing a submission.",
    "ethics_concern": "Thank you for bringing this concern to our attention.",
    "technical_issue": "Thank you for reporting this technical issue.",
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

        # Verbatim policy text, one paragraph per chunk, each tagged with its id
        # so the citation is visible inline as well as in the citations list.
        policy_blocks = [
            f"{chunk.content} [{chunk.policy_id}]" for chunk in retrieved_chunks
        ]
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
