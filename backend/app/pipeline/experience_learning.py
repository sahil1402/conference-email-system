"""Continual Experience Learning (CEL): learn a candidate internal policy from a
chair's [CHAIR:]-gap-filling edit.

``learn_from_edit`` is a best-effort BACKGROUND stage: it never raises, so it
can be fired-and-forgotten after a chair approves an edited draft without ever
disturbing the approve/send path. It runs at most ONE gated LLM call
(``_judge_and_condense``) to decide whether the chair's edit encodes a REUSABLE
policy or was ticket-specific, then (only if reusable) dedups against pending/
rejected suggestions, runs the existing conflict detector against the live KB,
and persists one ``policy_suggestions`` row for chair review.

Dispatch mirrors ``app.pipeline.policy_conflict``'s pattern exactly (same
provider gate, same strict-JSON-or-None contract) but is defined locally here
rather than reusing that module's private helpers, since the judge+condense
prompt shape differs from the conflict-check prompt shape. ``policy_conflict.py``
itself is not modified.
"""
import json
import logging

import httpx

from app.core.config import settings
from app.db.database import async_session_factory
from app.pipeline.drafter import find_placeholders
from app.pipeline.openai_compat import post_chat
from app.pipeline.policy_conflict import CONFLICT_TOP_K, detect_conflicts
from app.pipeline.retriever import PolicyRetriever
from app.pipeline.taxonomy import VALID_INTENTS
from app.repositories.email_repository import EmailRepository
from app.repositories.suggestion_repository import SuggestionRepository

logger = logging.getLogger(__name__)

_email_repo = EmailRepository()
_sugg_repo = SuggestionRepository()

_TIMEOUT_SECONDS = 60.0
_MAX_TOKENS = 2000

_SYSTEM = (
    "You analyze how a conference program chair edited an AI draft to fill a "
    "[CHAIR: ...] gap. Decide if the knowledge the chair supplied is a REUSABLE "
    "policy (applies to future tickets) or a ONE-OFF (ticket-specific, won't "
    "recur). If reusable, condense it into a concise, additive internal policy "
    "stated as a general rule with all ticket specifics removed. Never invent "
    "facts beyond what the chair's edit states.\n"
    "Respond with STRICT JSON and nothing else, in exactly this shape:\n"
    '{"generalizable": bool, "reason": "<why>", "confidence": <0-1 float>, '
    '"title": "<short policy title, empty string if not generalizable>", '
    '"content": "<condensed general rule, empty string if not generalizable>", '
    '"category": "<category or null>", "intents": ["<taxonomy intent>", ...], '
    '"experience_summary": "<one-line summary of what happened, for the chair>"}'
)


def _build_user_prompt(
    *, original_draft: str, final_text: str, inquiry: str, intent: str, policy_context: str
) -> str:
    return (
        f"REQUESTER ASKED:\n{inquiry}\n\nINTENT: {intent}\n\n"
        f"AI DRAFT (with the gap):\n{original_draft}\n\nCHAIR'S FINAL TEXT:\n{final_text}\n\n"
        f"EXISTING POLICY CONTEXT (do not duplicate):\n{policy_context}\n\n"
        "Return ONLY the JSON object described."
    )


def _extract_json(text: str) -> dict | None:
    """Pull the JSON object out of the model text (tolerates prose around it)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


async def _call_local(user_prompt: str) -> str | None:
    base = settings.LOCAL_MODEL_BASE_URL.rstrip("/")
    payload = {
        "model": settings.LOCAL_MODEL_NAME,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": _MAX_TOKENS,
        "temperature": settings.DRAFTER_TEMPERATURE,
        "seed": settings.DRAFTER_SEED,
        "stream": False,
    }
    headers = (
        {"Authorization": f"Bearer {settings.LOCAL_MODEL_API_KEY}"}
        if settings.LOCAL_MODEL_API_KEY
        else None
    )
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await post_chat(client, f"{base}/chat/completions", payload, headers)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def _call_anthropic(user_prompt: str) -> str | None:
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        return None
    from anthropic import AsyncAnthropic  # lazy — SDK optional at import time

    client = AsyncAnthropic(api_key=api_key)
    message = await client.messages.create(
        model=settings.DRAFT_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(b.text for b in message.content if b.type == "text")


async def _call_judge_model(user_prompt: str) -> str | None:
    """Raw model text, or None when no real LLM is configured / the call fails."""
    provider = settings.MODEL_PROVIDER
    try:
        if provider in ("anthropic", "anthropic_api"):
            return await _call_anthropic(user_prompt)
        if provider == "local":
            return await _call_local(user_prompt)
        # template / fallback / unrecognized → no real LLM available.
        return None
    except Exception as exc:  # noqa: BLE001 - CEL judge must never raise
        logger.warning("CEL judge model call failed (%s: %s).", type(exc).__name__, exc)
        return None


async def _judge_and_condense(
    *, original_draft: str, final_text: str, inquiry: str, intent: str, policy_context: str
) -> dict | None:
    """One gated call: judge generalizable-vs-one-off and condense a policy.

    Dispatch mirrors ``policy_conflict._call_model`` (anthropic/local else
    None). Returns the parsed dict on success, or ``None`` on any gate failure,
    HTTP/SDK error, or unparseable/invalid model output — never raises.
    """
    provider = settings.MODEL_PROVIDER
    if provider not in ("anthropic", "anthropic_api", "local"):
        return None
    user_prompt = _build_user_prompt(
        original_draft=original_draft, final_text=final_text,
        inquiry=inquiry, intent=intent, policy_context=policy_context,
    )
    try:
        raw = await _call_judge_model(user_prompt)
        if not raw:
            return None
        data = _extract_json(raw)
        if data is None or not isinstance(data, dict) or "generalizable" not in data:
            return None
        if data.get("generalizable"):
            title = str(data.get("title") or "").strip()
            content = str(data.get("content") or "").strip()
            if not title or not content:
                return None
            data["title"] = title
            data["content"] = content
            data["intents"] = [i for i in (data.get("intents") or []) if i in VALID_INTENTS]
        return data
    except Exception:  # noqa: BLE001 - gated best-effort, never raise
        logger.warning("CEL judge_and_condense failed", exc_info=True)
        return None


async def _gather_conflict_candidates(session_factory, title: str, content: str) -> list[dict]:
    """Top-``CONFLICT_TOP_K`` similar ACTIVE policies for the conflict check.

    Built with a fresh BM25 ``PolicyRetriever`` bound to the SAME
    ``session_factory`` passed to ``learn_from_edit`` — deliberately not the
    process-wide ``get_retriever()`` singleton (which is bound to the global
    ``async_session_factory`` regardless of what session this call should use,
    e.g. in tests). Best-effort: any failure here just yields no candidates.
    """
    try:
        retriever = PolicyRetriever(backend="bm25", session_factory=session_factory)
        hits = await retriever.retrieve(f"{title} {content}", intent="", top_k=CONFLICT_TOP_K)
        return [{"policy_key": h.policy_id, "title": h.title, "content": h.content} for h in hits]
    except Exception:  # noqa: BLE001 - conflict-candidate lookup is advisory
        logger.warning("CEL conflict-candidate lookup failed", exc_info=True)
        return []


async def learn_from_edit(email_id: str, *, session_factory=async_session_factory) -> None:
    """Best-effort background CEL stage for one email's chair-edited draft.

    Never raises: any failure is logged as a warning and the function returns,
    so this can be fired-and-forgotten from the approve path.
    """
    try:
        async with session_factory() as db:
            email = await _email_repo.get_email_by_id(db, email_id)
            if email is None:
                return
            draft = email.draft or {}
            if not draft.get("is_edited"):
                return
            original = draft.get("original_draft_text") or ""
            final = draft.get("draft_text") or ""
            if not find_placeholders(original):
                # No [CHAIR: ...] gap was filled — nothing for CEL to learn.
                return

            intent = (email.classification or {}).get("intent", "")
            inquiry = f"{email.subject}\n{email.body or ''}".strip()
            retrieved_ids = (email.retrieval_context or {}).get("retrieved_ids") or []
            policy_context = json.dumps(retrieved_ids)

            cand = await _judge_and_condense(
                original_draft=original, final_text=final,
                inquiry=inquiry, intent=intent, policy_context=policy_context,
            )
            if not cand or not cand.get("generalizable"):
                return

            title = str(cand.get("title") or "").strip()
            content = str(cand.get("content") or "").strip()
            if not title or not content:
                return
            intents = [i for i in (cand.get("intents") or []) if i in VALID_INTENTS]

            dup = await _sugg_repo.find_similar(db, title=title, content=content)
            if dup is not None:
                await _sugg_repo.bump_seen(db, dup.id)
                return

            candidates = await _gather_conflict_candidates(session_factory, title, content)
            report = await detect_conflicts(title=title, content=content, candidates=candidates)

            await _sugg_repo.create(
                db,
                source_email_id=int(email.id),
                experience_summary=cand.get("experience_summary") or "",
                title=title,
                content=content,
                category=cand.get("category"),
                intents=intents,
                generalizable=True,
                reason=cand.get("reason"),
                confidence=cand.get("confidence"),
                conflict_report=report.model_dump() if report is not None else None,
            )
    except Exception:  # noqa: BLE001 - CEL must never disturb the caller
        logger.warning("CEL learn_from_edit failed for email_id=%s", email_id, exc_info=True)
