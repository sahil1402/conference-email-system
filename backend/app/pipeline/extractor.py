"""Extractor (which submission an email is about, and who it names).

Turns the distiller's RAW identification lines into validated, deduplicated
structures. The split is deliberate: :mod:`app.pipeline.distiller` is the
transport parser and keeps whatever the model emitted verbatim, so a malformed
value survives the wire; this module is the only place that decides what a
value MEANS. Keeping validation here means a bad value can be inspected (and
later counted) with the full picture, instead of vanishing at parse time where
it would look identical to the model never emitting the line at all.

Failure policy: best-effort, like the distiller. ``extract`` never raises — a
normalization bug degrades to an empty result rather than breaking the pipeline
that called it, since knowing which paper an email is about is an enrichment,
never a precondition for drafting a reply.

Scope note: only the LLM path (subtask 2a) is implemented. When the distiller
did not run at all, this returns an empty ``method="none"`` result; the regex
fallback that will fill that branch is subtask 2b.
"""

import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.pipeline.distiller import DistillResult

logger = logging.getLogger(__name__)

_AUTHOR_FIELD_SEPARATOR = "|"
# The model is told to emit exactly `name | email | affiliation`.
_AUTHOR_FIELD_COUNT = 3

ExtractionMethod = Literal["llm_distiller", "regex_fallback", "none"]


class AuthorMention(BaseModel):
    """One person an email identifies. Every field is independently optional.

    A mention with only a name is still useful (it is who wrote in), so a
    missing piece never invalidates the rest of the entry. An entry with no
    populated field at all carries no information and is dropped upstream in
    :func:`_parse_author`.
    """

    name: str | None = Field(default=None, description="Display name, if given.")
    email: str | None = Field(default=None, description="Email address, if given.")
    affiliation: str | None = Field(
        default=None, description="Institution/affiliation, if given."
    )


class ExtractionResult(BaseModel):
    """Which submission an email is about, and who it names.

    ``method`` records HOW the values were obtained, not how well: an
    ``llm_distiller`` result with everything empty means the model looked and
    found nothing, which is a real finding and distinct from ``none`` (nothing
    looked at all).
    """

    submission_number: str | None = Field(
        default=None,
        description="Submission/paper number as identified, or None.",
    )
    openreview_forum_id: str | None = Field(
        default=None,
        description="OpenReview forum id as identified, or None.",
    )
    authors: list[AuthorMention] = Field(
        default_factory=list,
        description="People the email identifies, deduplicated, in first-seen "
        "order.",
    )
    method: ExtractionMethod = Field(
        default="none",
        description="Which path produced this result.",
    )


def _field_or_none(value: str) -> str | None:
    """Normalize one raw author field: blank or the ``NONE`` sentinel → None."""
    cleaned = value.strip()
    if not cleaned or cleaned.upper() == "NONE":
        return None
    return cleaned


def _parse_author(raw: str) -> AuthorMention | None:
    """Parse one raw ``name | email | affiliation`` string; None if empty.

    Malformed input is parsed BEST-EFFORT rather than skipped, because the
    distiller deliberately forwards malformed lines here to be salvaged, and a
    dropped line silently loses a real person:

    * Fewer than three fields — the missing trailing fields become None, so a
      bare ``John Doe`` reads as a name-only mention. This is the same shape as
      an obedient ``John Doe | NONE | NONE``, which is the point: the model
      omitting separators should not change the meaning.
    * More than three fields — the surplus is rejoined into the affiliation
      rather than truncated away. A stray separator is far likelier to sit
      inside an affiliation (``Dept | Univ``) than to signal a fourth value, and
      dropping the tail would silently discard real text.
    * Every field empty — returns None; an entry that identifies nobody is not
      a mention.
    """
    parts = raw.split(_AUTHOR_FIELD_SEPARATOR)
    if len(parts) > _AUTHOR_FIELD_COUNT:
        head = parts[: _AUTHOR_FIELD_COUNT - 1]
        tail = _AUTHOR_FIELD_SEPARATOR.join(parts[_AUTHOR_FIELD_COUNT - 1 :])
        parts = [*head, tail]
    parts += [""] * (_AUTHOR_FIELD_COUNT - len(parts))

    mention = AuthorMention(
        name=_field_or_none(parts[0]),
        email=_field_or_none(parts[1]),
        affiliation=_field_or_none(parts[2]),
    )
    if mention.name is None and mention.email is None and mention.affiliation is None:
        return None
    return mention


def _dedupe_authors(mentions: list[AuthorMention]) -> list[AuthorMention]:
    """Drop repeats on (name, email), case-insensitively, keeping first-seen.

    ``casefold`` rather than ``lower``: real ticket traffic carries non-ASCII
    author names, and only casefold folds them correctly.

    Affiliation is NOT part of the key — the same person written once with and
    once without their institution is one person. First-seen wins so the order
    the model reported (sender first, per the prompt) is preserved.
    """
    seen: set[tuple[str | None, str | None]] = set()
    unique: list[AuthorMention] = []
    for mention in mentions:
        key = (
            mention.name.casefold() if mention.name else None,
            mention.email.casefold() if mention.email else None,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(mention)
    return unique


class EmailExtractor:
    """Resolves which submission an email is about and who it names."""

    def extract(
        self,
        subject: str,
        body: str,
        sender: str,
        sender_name: str | None,
        distilled: DistillResult | None,
    ) -> ExtractionResult:
        """Best-effort extraction. Never raises.

        ``subject`` / ``body`` / ``sender`` / ``sender_name`` are accepted now
        and unused on the LLM path — the model already read the subject and body
        itself. They are what the subtask-2b regex fallback will read, and they
        are in the signature from the start so wiring this in does not later
        require touching every call site.

        When ``distilled`` is present it is trusted outright, INCLUDING when it
        found nothing: the prompt directs the model to read both subject and
        body and to answer NONE when there is genuinely no identifier, so an
        empty LLM result is an answer, not a failure to re-litigate.
        """
        try:
            if distilled is None:
                # TODO(subtask 2b): regex fallback over subject/body/sender.
                # Until then, no distiller output means nothing was examined,
                # which is exactly what method="none" records.
                return ExtractionResult(method="none")

            authors = [
                mention
                for raw in distilled.authors_raw
                if (mention := _parse_author(raw)) is not None
            ]
            return ExtractionResult(
                submission_number=distilled.submission_number_raw,
                openreview_forum_id=distilled.openreview_id_raw,
                authors=_dedupe_authors(authors),
                method="llm_distiller",
            )
        except Exception as exc:  # noqa: BLE001 - extraction must never raise
            logger.warning(
                "Extraction failed (%s: %s); returning an empty result.",
                type(exc).__name__,
                exc,
            )
            return ExtractionResult(method="none")
