"""Drafter (grounded reply generation).

Generates a reply draft strictly grounded in the retrieved policy chunks. The
system prompt forbids inventing policies; the user prompt hands the model the
original email, the classification, and the retrieved policy context. The
drafter runs BEFORE the router (it has no notion of "lane") — the router then
decides FAQ vs. human-review from the draft's own completeness/groundedness/
self-rated confidence (see app.pipeline.router).

Provider behaviour:
- No API key configured → returns a deterministic fallback (no network call).
- Any error during generation → returns a fallback with the error captured in
  generation_metadata, never raising (the orchestrator decides what to do).

The model id is read from settings.DRAFT_MODEL — never hardcoded here — so it
stays swappable and so design docs/source carry no fixed model name.
"""

import logging
import re
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from app.core.config import settings
from app.pipeline.openai_compat import post_chat

logger = logging.getLogger(__name__)

# Style-guide file contents cached per path; missing files warn once.
_style_guide_cache: dict[str, str] = {}
_style_guide_warned: set[str] = set()

# Local-provider HTTP timeout (OpenAI-compatible endpoint, e.g. Ollama).
_LOCAL_TIMEOUT_SECONDS = 60.0

# Matches knowledge-base policy ids like "policy_001" or internal policy keys like
# "int_deadline-extended" wherever they appear in the generated draft, so we can
# surface them as explicit citations or scrub them from requester-facing text.
_CITATION_PATTERN = re.compile(r"\b(?:policy_\d+|int_[a-z0-9-]+)")

_SYSTEM_PROMPT = """\
You are a professional assistant to a conference program chair, drafting replies \
to emails from authors and reviewers.

Rules you must follow without exception:
- Ground every statement in the policy context provided in the user message.
- Never invent, assume, or generalize a policy that is not in that context.
- Be concise, professional, and direct.
- Answer only the question(s) the requester actually asked. Use the policy \
context solely to answer those question(s); do not volunteer additional policy \
facts on topics the requester did not raise, even if they appear in the context \
and are correct — answer every part of a multi-part question, but nothing beyond it.
- The REPLY section must contain ONLY the email text the requester should \
receive: no headers like "Draft reply:", no meta-commentary, no chair notes.
- Write the reply as a chair with full knowledge would. Never tell the \
requester that the policy or context does not specify or cover something.
- Never claim an action has been taken ("we have updated / forwarded / \
fixed...") and never promise one ("we will look into / check / follow up") — \
the draft cannot perform actions. Where the request requires an operational \
step, place a [CHAIR: ...] placeholder for its outcome instead.
- Where the context cannot support something the reply needs — a fact, a \
procedure, a decision — do not guess and do not mention the gap: insert an \
inline placeholder at that exact spot, formatted [CHAIR: <short hint, a few \
words>], keeping the surrounding sentence natural so the chair only edits \
the bracketed part.
- Use placeholders sparingly: at most one per distinct question or decision \
the requester actually raised — merge related unknowns into one. If the \
context supports almost none of the answer, write a brief courteous reply \
around a single placeholder for the whole answer, not a scaffold of many. \
Never add a placeholder for something the requester did not ask.
- NOTES FOR CHAIR: one short line per placeholder — the gap plus your \
suggested resolution if you can infer one; telegraphic style, no category \
labels, no restating the hint. Other caveats the chair should handle also \
go here (one line each) — never in the reply.
- Never mention internal policy ids (like policy_004) in the reply.

Output EXACTLY this structure:
=== REPLY ===
<the reply email text>
=== CITATIONS ===
<comma-separated internal ids of the policy chunks you relied on \
(e.g. policy_004, policy_012), or "none">
=== NOTES FOR CHAIR ===
<anything the chair should verify or decide before sending, or "none">
=== CONFIDENCE ===
<a single number 0.0-1.0: your confidence that this REPLY fully and correctly \
answers every part of the requester's question using ONLY the provided policy \
context, with no remaining gaps. Use a high value only when the reply is \
complete and grounded; if you left any placeholder or note, this must be low.>"""

# Structured-output sections emitted per the system prompt above. `notes` is
# non-greedy so the optional trailing CONFIDENCE group can claim the tail of
# the text when present; the `\Z` anchor is required for that non-greedy
# group to still expand all the way to the end of the string when no
# CONFIDENCE section is present (otherwise the lazy quantifier combined with
# a wholly-optional trailing group would match `notes` as empty every time —
# verified empirically; see task-F1-report.md).
_SECTION_RE = re.compile(
    r"===\s*REPLY\s*===\s*(?P<reply>.*?)"
    r"\s*===\s*CITATIONS\s*===\s*(?P<cites>.*?)"
    r"\s*===\s*NOTES\s+FOR\s+CHAIR\s*===\s*(?P<notes>.*?)"
    r"(?:\s*===\s*CONFIDENCE\s*===\s*(?P<confidence>.*))?\Z",
    re.DOTALL | re.IGNORECASE,
)
# Inline internal ids — parenthesized citation groups first, then bare ids.
# Matches both policy_NNN and int_<slug> keys for scrubbing from requester text.
_INLINE_ID_RE = re.compile(
    r"\s*\((?:see\s+)?(?:policy_\d+|int_[a-z0-9-]+)"
    r"(?:\s*,\s*(?:policy_\d+|int_[a-z0-9-]+))*\)"
    r"|\b(?:policy_\d+|int_[a-z0-9-]+)\b"
)
_SCAFFOLD_RE = re.compile(r"^\s*(?:draft\s+reply|reply|draft)\s*:\s*\n+", re.IGNORECASE)

# The style guide instructs the model to sign off with the literal placeholder
# "[Sender name]" (see data/style_guide/*). Interim, until the authenticated-chair
# account lands, substitute a fixed chair name when finalizing the LLM reply —
# the prompt is unchanged; only the parsed response text is rewritten.
_SENDER_NAME = "Marc Pujol-Gonzalez"
_SENDER_PLACEHOLDER_RE = re.compile(r"\[\s*sender[\s_]?name\s*\]", re.IGNORECASE)

# Chair-editable placeholders the drafter inserts where the context cannot
# support a statement. Public: the approve endpoint uses it as the send-gate.
PLACEHOLDER_RE = re.compile(r"\[CHAIR:\s*(?P<hint>[^\]]*)\]")

# Chair-facing meta language that must never reach a requester — the reply
# contract routes it to placeholders/notes, and these flag any residual leaks.
_LEAK_PATTERNS = (
    re.compile(
        r"(?:do(?:es)?\s+not|doesn'?t|don'?t)\s+"
        r"(?:specify|state|address|cover|mention)|not\s+specified",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwe\s+(?:will|'ll)\s+(?:look\s+into|check|verify|confirm|"
        r"investigate|follow\s+up)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:cannot|can'?t|unable\s+to)\s+(?:confirm|verify|determine)\b",
        re.IGNORECASE,
    ),
    # Meta references to the retrieval context itself ("the policy context/
    # information available to me...") — internal machinery, not requester text.
    re.compile(r"\bpolicy\s+(?:context|information|text)\b", re.IGNORECASE),
)


def find_placeholders(text: str) -> list[str]:
    """Return the hint of every [CHAIR: ...] placeholder in ``text``, in order."""
    return [m.group("hint").strip() for m in PLACEHOLDER_RE.finditer(text)]


def _apply_reply_contract(
    reply: str, notes: str | None, metadata: dict
) -> tuple[list[str], str | None]:
    """Deterministically enforce the reply contract on a parsed reply.

    Extracts [CHAIR: ...] placeholders (returned so the orchestrator can force
    the human-review lane) and flags residual chair-facing meta language into
    ``metadata`` plus a warning appended to the chair notes. Flag, never
    rewrite — regex edits to prose are how replies get garbled.
    """
    placeholders = find_placeholders(reply)
    leaks: list[str] = []
    for pattern in _LEAK_PATTERNS:
        leaks.extend(m.group(0) for m in pattern.finditer(reply))
    if leaks:
        metadata["reply_leaks"] = leaks
        warning = (
            "WARNING (automated check): the reply may contain chair-facing "
            "meta language that belongs in a [CHAIR: ...] placeholder or in "
            "these notes — please rephrase before sending: "
            + "; ".join(f'"{s}"' for s in leaks)
        )
        notes = f"{notes}\n\n{warning}" if notes else warning
    return placeholders, notes


def _sanitize_reply(reply: str) -> str:
    """Deterministically enforce reply hygiene, whatever the model produced.

    Strips leading scaffold headers ("Draft reply:") and any internal policy
    ids — those are internal indexing and must never reach a requester.
    """
    reply = _SCAFFOLD_RE.sub("", reply)
    reply = _INLINE_ID_RE.sub("", reply)
    reply = _SENDER_PLACEHOLDER_RE.sub(_SENDER_NAME, reply)  # fill the sign-off name
    reply = re.sub(r"[ \t]+([.,;:!?])", r"\1", reply)  # tidy space before punct
    reply = re.sub(r"[ \t]{2,}", " ", reply)
    return reply.strip()


def _parse_confidence(raw: str | None) -> float | None:
    """Parse a self-rated confidence number, clamped to [0, 1].

    Returns None on missing/unparseable input — the safe default for a
    router precondition that gates FAQ eligibility.
    """
    if not raw:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", raw)
    if not m:
        return None
    try:
        return max(0.0, min(1.0, float(m.group(0))))
    except ValueError:
        return None


def _split_structured(text: str) -> tuple[str, list[str], str | None, float | None]:
    """Split model output into (reply, citations, notes_for_chair, answer_confidence).

    Falls back gracefully on unstructured output (older prompts, small local
    models): the whole text is treated as the reply and citations are parsed
    from it before sanitization strips them; notes and confidence are None
    in that no-match branch.
    """
    match = _SECTION_RE.search(text)
    if not match:
        return _sanitize_reply(text), _parse_citations(text), None, None
    citations: list[str] = []
    for cid in _CITATION_PATTERN.findall(match.group("cites")):
        if cid not in citations:
            citations.append(cid)
    notes = match.group("notes").strip()
    if notes.lower() in ("", "none", "n/a", "none."):
        notes = None
    confidence = _parse_confidence(match.group("confidence"))
    return _sanitize_reply(match.group("reply")), citations, notes, confidence


def _load_style_guide() -> str | None:
    """Return the configured style guide's text, or None when absent.

    Cached per path so the file is read once per process. An unreadable path
    logs one warning and drafting proceeds without a guide — never raises.
    """
    path = settings.STYLE_GUIDE_PATH
    if not path:
        return None
    if path in _style_guide_cache:
        return _style_guide_cache[path]
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        if path not in _style_guide_warned:
            _style_guide_warned.add(path)
            logger.warning(
                "STYLE_GUIDE_PATH %r is not readable; drafting without a style guide.",
                path,
            )
        return None
    _style_guide_cache[path] = text
    return text


def _system_prompt() -> str:
    """Base grounding rules, plus the style guide when one is configured.

    The guide is appended AFTER the grounding rules — it carries an explicit
    subordination clause, and the rules above always take precedence.
    """
    guide = _load_style_guide()
    if not guide:
        return _SYSTEM_PROMPT
    return f"{_SYSTEM_PROMPT}\n\n--- REPLY STYLE & INSTRUCTION GUIDE ---\n{guide}"


class DraftResponse(BaseModel):
    """Output of the drafter — the reply text and provenance metadata."""

    draft_text: str = Field(
        ...,
        description="The reply text only — requester-safe, no internal policy "
        "ids, no chair-facing commentary.",
    )
    notes_for_chair: str | None = Field(
        default=None,
        description="Chair-facing caveats/confirmations, kept STRICTLY out of "
        "draft_text so they can never be sent to a requester.",
    )
    placeholders: list[str] = Field(
        default_factory=list,
        description="Hints of the [CHAIR: ...] placeholders left in draft_text "
        "for the chair to fill in; non-empty forces the human-review lane and "
        "blocks approval until resolved.",
    )
    citations: list[str] = Field(
        default_factory=list,
        description="Policy ids the draft relied on (e.g. ['policy_004']); "
        "internal provenance, never shown in the reply text.",
    )
    answer_confidence: float | None = Field(
        default=None,
        description="Drafter self-rated confidence (0-1) that the reply fully and "
        "correctly answers the request from the provided context. None for non-LLM "
        "drafters or unparseable output. A router FAQ-lane precondition.",
    )
    model_used: str = Field(
        ..., description='Model id used, or "none" when no draft was generated.'
    )
    generation_metadata: dict = Field(
        default_factory=dict,
        description="Token usage, errors, and other generation context.",
    )


def _build_user_prompt(
    email: dict,
    classification,
    retrieved_chunks: list,
) -> str:
    """Assemble the grounded user prompt from all pipeline inputs."""
    sender = email.get("from") or email.get("sender") or "unknown"
    # Surface the sender's display name when the ingest provided one, so the
    # drafter can greet the requester by name (guide 1: never a placeholder).
    sender_name = email.get("sender_name")
    if sender_name:
        sender = f"{sender_name} <{sender}>"
    subject = email.get("subject", "")
    body = email.get("body", "")
    transcript = email.get("thread_transcript")

    context_blocks = []
    for chunk in retrieved_chunks:
        context_blocks.append(f"[{chunk.policy_id}] {chunk.title}\n{chunk.content}")
    context = "\n\n".join(context_blocks) if context_blocks else "(no policy context retrieved)"

    if transcript:
        email_block = (
            "--- CONVERSATION (oldest to newest) ---\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n"
            f"{transcript}\n\n"
            "Reply to the LATEST message from the requester. Do not repeat "
            "information already provided earlier in the conversation.\n\n"
        )
    else:
        email_block = (
            "--- ORIGINAL EMAIL ---\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n"
            f"Body: {body}\n\n"
        )

    return (
        f"{email_block}"
        "--- CLASSIFICATION ---\n"
        f"Intent: {classification.intent} (confidence: {classification.confidence:.2f})\n\n"
        "--- RETRIEVED POLICY CONTEXT ---\n"
        f"{context}\n\n"
        "--- TASK ---\n"
        "Using only the policy context above for grounding, answer only the "
        "question(s) the requester raised — nothing more."
    )


def _parse_citations(text: str) -> list[str]:
    """Extract unique policy ids from the draft, preserving first-seen order."""
    seen: list[str] = []
    for match in _CITATION_PATTERN.findall(text):
        if match not in seen:
            seen.append(match)
    return seen


def _fallback(text: str, extra: dict | None = None) -> DraftResponse:
    """Build a deterministic fallback draft (never raises, no network)."""
    metadata = {}
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
    ) -> DraftResponse:
        """Generate a grounded draft via the configured provider, or fall back."""
        # The template provider makes no model call, so it skips prompt assembly.
        if self.provider == "template":
            return self._draft_template(email, classification, retrieved_chunks)

        user_prompt = _build_user_prompt(email, classification, retrieved_chunks)

        if self.provider in ("anthropic", "anthropic_api"):
            return await self._draft_anthropic(user_prompt)
        if self.provider == "local":
            return await self._draft_local(user_prompt)
        # "fallback" or any unrecognized value.
        return _fallback("Draft unavailable — model provider set to fallback.")

    def _draft_template(
        self, email: dict, classification, retrieved_chunks: list
    ) -> DraftResponse:
        """Fill a response template from retrieved policy chunks — zero model call.

        Delegates to ``TemplateDrafter`` (imported lazily to avoid an import
        cycle: template_drafter imports DraftResponse from this module). Fully
        offline; never raises.
        """
        from app.pipeline.template_drafter import TemplateDrafter

        return TemplateDrafter().draft(email, classification.intent, retrieved_chunks)

    async def _draft_anthropic(self, user_prompt: str) -> DraftResponse:
        """Draft via the hosted Anthropic API, falling back on missing key/error."""
        api_key = settings.ANTHROPIC_API_KEY

        # No key configured → deterministic fallback, no network call.
        if not api_key:
            return _fallback("Draft unavailable — API key not configured.")

        try:
            # Imported lazily so the module imports cleanly without the SDK
            # installed (e.g. in environments that never call the drafter).
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=api_key)
            message = await client.messages.create(
                model=settings.DRAFT_MODEL,
                max_tokens=settings.DRAFTER_MAX_TOKENS,
                system=_system_prompt(),
                messages=[{"role": "user", "content": user_prompt}],
            )

            raw_text = "".join(
                block.text for block in message.content if block.type == "text"
            )
            reply, citations, notes, confidence = _split_structured(raw_text)
            metadata = {
                "stop_reason": message.stop_reason,
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            }
            placeholders, notes = _apply_reply_contract(reply, notes, metadata)

            return DraftResponse(
                draft_text=reply,
                notes_for_chair=notes,
                placeholders=placeholders,
                citations=citations,
                answer_confidence=confidence,
                model_used=settings.DRAFT_MODEL,
                generation_metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 - drafter must never raise
            return _fallback(
                "Draft unavailable — an error occurred during generation.",
                {"error": str(exc), "error_type": type(exc).__name__},
            )

    async def _draft_local(self, user_prompt: str) -> DraftResponse:
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
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": settings.DRAFTER_MAX_TOKENS,
            # Determinism: temperature 0 (greedy) + a fixed seed. Reasoning models
            # reject a non-default temperature — _post_chat drops it and retries.
            "temperature": settings.DRAFTER_TEMPERATURE,
            "seed": settings.DRAFTER_SEED,
            "stream": False,
        }
        # Bearer auth when the endpoint is a hosted keyed service; local
        # unauthenticated servers leave LOCAL_MODEL_API_KEY unset.
        headers = (
            {"Authorization": f"Bearer {settings.LOCAL_MODEL_API_KEY}"}
            if settings.LOCAL_MODEL_API_KEY
            else None
        )

        try:
            async with httpx.AsyncClient(timeout=_LOCAL_TIMEOUT_SECONDS) as client:
                response = await post_chat(client, url, payload, headers)
                response.raise_for_status()
                data = response.json()

            raw_text = data["choices"][0]["message"]["content"]
            reply, citations, notes, confidence = _split_structured(raw_text)
            metadata = {"provider": "local"}
            usage = data.get("usage")
            if isinstance(usage, dict):
                metadata["input_tokens"] = usage.get("prompt_tokens")
                metadata["output_tokens"] = usage.get("completion_tokens")
            placeholders, notes = _apply_reply_contract(reply, notes, metadata)

            return DraftResponse(
                draft_text=reply,
                notes_for_chair=notes,
                placeholders=placeholders,
                citations=citations,
                answer_confidence=confidence,
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
                {"error": str(exc), "error_type": type(exc).__name__, "provider": "local"},
            )
