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

import logging
import re

import httpx
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

# Local-provider HTTP timeout (OpenAI-compatible endpoint, e.g. Ollama).
_LOCAL_TIMEOUT_SECONDS = 60.0

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


def _fallback(text: str, routing, extra: dict | None = None) -> DraftResponse:
    """Build a deterministic fallback draft (never raises, no network)."""
    metadata = {"lane": routing.lane}
    if extra:
        metadata.update(extra)
    return DraftResponse(
        draft_text=text,
        citations=[],
        model_used="none",
        generation_metadata=metadata,
    )


class ResponseDrafter:
    """Generates grounded reply drafts via the configured AI provider.

    Provider is selected from ``MODEL_PROVIDER`` (passed in by the orchestrator):
    ``anthropic``/``anthropic_api`` → hosted Anthropic API, ``local`` → an
    OpenAI-compatible endpoint (e.g. Ollama), anything else → deterministic
    fallback. Every path returns a ``DraftResponse`` and never raises, so the
    orchestrator can treat drafting as best-effort.
    """

    def __init__(self, provider: str = "anthropic") -> None:
        self.provider = provider

    async def draft(
        self,
        email: dict,
        classification,
        retrieved_chunks: list,
        routing,
    ) -> DraftResponse:
        """Generate a grounded draft via the configured provider, or fall back."""
        user_prompt = _build_user_prompt(
            email, classification, retrieved_chunks, routing
        )

        if self.provider in ("anthropic", "anthropic_api"):
            return await self._draft_anthropic(user_prompt, routing)
        if self.provider == "local":
            return await self._draft_local(user_prompt, routing)
        # "fallback" or any unrecognized value.
        return _fallback(
            "Draft unavailable — model provider set to fallback.", routing
        )

    async def _draft_anthropic(self, user_prompt: str, routing) -> DraftResponse:
        """Draft via the hosted Anthropic API, falling back on missing key/error."""
        api_key = settings.ANTHROPIC_API_KEY

        # No key configured → deterministic fallback, no network call.
        if not api_key:
            return _fallback("Draft unavailable — API key not configured.", routing)

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
            return _fallback(
                "Draft unavailable — an error occurred during generation.",
                routing,
                {"error": str(exc), "error_type": type(exc).__name__},
            )

    async def _draft_local(self, user_prompt: str, routing) -> DraftResponse:
        """Draft via an OpenAI-compatible local endpoint (e.g. Ollama).

        POSTs to ``{LOCAL_MODEL_BASE_URL}/chat/completions`` with the same
        system+user prompt structure as the Anthropic path. On *any* httpx /
        parsing error the system degrades gracefully to a fallback draft and
        logs a warning — it never raises.
        """
        base = settings.LOCAL_MODEL_BASE_URL.rstrip("/")
        url = f"{base}/chat/completions"
        payload = {
            "model": settings.LOCAL_MODEL_NAME,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": settings.DRAFTER_MAX_TOKENS,
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=_LOCAL_TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()

            draft_text = data["choices"][0]["message"]["content"]
            metadata = {"lane": routing.lane, "provider": "local"}
            usage = data.get("usage")
            if isinstance(usage, dict):
                metadata["input_tokens"] = usage.get("prompt_tokens")
                metadata["output_tokens"] = usage.get("completion_tokens")

            return DraftResponse(
                draft_text=draft_text,
                citations=_parse_citations(draft_text),
                model_used=settings.LOCAL_MODEL_NAME,
                generation_metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 - drafter must never raise
            logger.warning(
                "Local model draft failed (%s: %s); falling back.",
                type(exc).__name__,
                exc,
            )
            return _fallback(
                "Draft unavailable — local model error.",
                routing,
                {"error": str(exc), "error_type": type(exc).__name__, "provider": "local"},
            )
