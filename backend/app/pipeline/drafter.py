"""Drafter (grounded reply generation).

Generates a reply draft strictly grounded in the retrieved policy chunks. The
system prompt forbids inventing policies; the user prompt hands the model the
original email, the classification, the retrieved policy context, and the
routing decision so it can tailor an FAQ answer vs. a human-review starting
draft.

Provider behaviour:
- No API key configured → returns a deterministic fallback (no network call).
- Any error during generation → returns a fallback with the error captured in
  generation_metadata, never raising (the orchestrator decides what to do).

The model id is read from settings.DRAFT_MODEL — never hardcoded here — so it
stays swappable and so design docs/source carry no fixed model name.
"""

import re

from pydantic import BaseModel, Field

from app.core.config import settings

# Matches knowledge-base policy ids like "policy_001" wherever they appear in
# the generated draft, so we can surface them as explicit citations.
_CITATION_PATTERN = re.compile(r"policy_\d+")

_SYSTEM_PROMPT = """\
You are a professional assistant to a conference program chair, drafting replies \
to emails from authors and reviewers.

Rules you must follow without exception:
- Ground every statement in the policy context provided in the user message.
- Never invent, assume, or generalize a policy that is not in that context.
- If the context does not answer the question, say so plainly and do not guess.
- Be concise (under 200 words), professional, and direct.
- For the FAQ lane: give a complete, final answer the author can act on.
- For the human-review lane: write a starting draft for the chair and flag any \
uncertainty or missing information the chair should confirm before sending.
- Cite the policy ids you relied on (e.g. policy_004) inline where relevant."""


class DraftResponse(BaseModel):
    """Output of the drafter — the reply text and provenance metadata."""

    draft_text: str = Field(..., description="The generated reply text.")
    citations: list[str] = Field(
        default_factory=list,
        description="Policy ids referenced in the draft (e.g. ['policy_004']).",
    )
    model_used: str = Field(
        ..., description='Model id used, or "none" when no draft was generated.'
    )
    generation_metadata: dict = Field(
        default_factory=dict,
        description="Token usage, lane, errors, and other generation context.",
    )


def _build_user_prompt(
    email: dict,
    classification,
    retrieved_chunks: list,
    routing,
) -> str:
    """Assemble the grounded user prompt from all pipeline inputs."""
    sender = email.get("from") or email.get("sender") or "unknown"
    subject = email.get("subject", "")
    body = email.get("body", "")

    context_blocks = []
    for chunk in retrieved_chunks:
        context_blocks.append(f"[{chunk.policy_id}] {chunk.title}\n{chunk.content}")
    context = "\n\n".join(context_blocks) if context_blocks else "(no policy context retrieved)"

    return (
        "--- ORIGINAL EMAIL ---\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Body: {body}\n\n"
        "--- CLASSIFICATION ---\n"
        f"Intent: {classification.intent} (confidence: {classification.confidence:.2f})\n\n"
        "--- RETRIEVED POLICY CONTEXT ---\n"
        f"{context}\n\n"
        "--- ROUTING ---\n"
        f"Lane: {routing.lane}\n"
        f"Reason: {routing.reason}\n\n"
        "--- TASK ---\n"
        "Draft a reply based only on the policy context above."
    )


def _parse_citations(text: str) -> list[str]:
    """Extract unique policy ids from the draft, preserving first-seen order."""
    seen: list[str] = []
    for match in _CITATION_PATTERN.findall(text):
        if match not in seen:
            seen.append(match)
    return seen


class ResponseDrafter:
    """Generates grounded reply drafts via the configured AI provider."""

    def __init__(self, provider: str = "anthropic") -> None:
        self.provider = provider

    async def draft(
        self,
        email: dict,
        classification,
        retrieved_chunks: list,
        routing,
    ) -> DraftResponse:
        """Generate a grounded draft, or a safe fallback on missing key/error."""
        api_key = settings.ANTHROPIC_API_KEY

        # No key configured → deterministic fallback, no network call.
        if not api_key:
            return DraftResponse(
                draft_text="Draft unavailable — API key not configured.",
                citations=[],
                model_used="none",
                generation_metadata={},
            )

        user_prompt = _build_user_prompt(
            email, classification, retrieved_chunks, routing
        )

        try:
            # Imported lazily so the module imports cleanly without the SDK
            # installed (e.g. in environments that never call the drafter).
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=api_key)
            message = await client.messages.create(
                model=settings.DRAFT_MODEL,
                max_tokens=settings.DRAFTER_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            draft_text = "".join(
                block.text for block in message.content if block.type == "text"
            )

            return DraftResponse(
                draft_text=draft_text,
                citations=_parse_citations(draft_text),
                model_used=settings.DRAFT_MODEL,
                generation_metadata={
                    "lane": routing.lane,
                    "stop_reason": message.stop_reason,
                    "input_tokens": message.usage.input_tokens,
                    "output_tokens": message.usage.output_tokens,
                },
            )
        except Exception as exc:  # noqa: BLE001 - drafter must never raise
            return DraftResponse(
                draft_text="Draft unavailable — an error occurred during generation.",
                citations=[],
                model_used="none",
                generation_metadata={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "lane": routing.lane,
                },
            )
