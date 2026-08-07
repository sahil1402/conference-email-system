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

Two paths, and they are mutually exclusive by design. When the distiller ran,
its answer is used outright; only when it did not run at all does a regex
fallback read the raw subject/body. Regex is deliberately NOT a supplement that
tops up a present LLM result — that would need per-field provenance to stay
honest, and ``method`` is a clean three-value record of which path produced the
whole result.

The fallback is tuned for PRECISION over recall. Roughly half of real threads
carry no submission reference at all, so returning nothing is the ordinary
outcome, not a failure; attaching the WRONG paper to a ticket is far more
costly to a chair than attaching none.
"""

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.pipeline.distiller import DistillResult

logger = logging.getLogger(__name__)

_AUTHOR_FIELD_SEPARATOR = "|"
# The model is told to emit exactly `name | email | affiliation`.
_AUTHOR_FIELD_COUNT = 3

ExtractionMethod = Literal["llm_distiller", "regex_fallback", "none"]

# --- regex-fallback patterns ------------------------------------------------
# Real submission numbers are 4-5 digits and are only trustworthy next to a cue
# word: an ungated \d{4,5} sweep pulls in years, dates, counts and phone
# fragments. `filler` captures at most ONE intervening word so the cue stays
# close, and is inspected afterwards — a number the cue introduces directly
# ("Submission 2026") is believed, one reached across a word ("paper due 2026")
# is not.
_SUBMISSION_CUE_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:paper|submission)s?"
    r"(?:\s*(?:id|no\.?|number))?"
    r"(?P<filler>(?:\s*[A-Za-z][A-Za-z.\-]{0,11}){0,1})"
    r"\s*[#:\-]?\s*"
    r"(?P<number>\d{4,5})"
    r"(?!\d)",
    re.IGNORECASE,
)
# A bare "#12345" is its own cue — no preceding word needed. The lookbehind
# keeps it out of URL fragments and anchors.
_HASH_NUMBER_RE = re.compile(r"(?<![\w/])#(?P<number>\d{4,5})(?!\d)")

# Anchored to the two link shapes that actually carry a forum id. `?id=` alone
# is NOT enough: AAAI's committee/group URLs (openreview.net/group?id=...) share
# that shape and are confirmed false positives in real traffic. The trailing
# lookahead rejects longer ids rather than silently truncating them to 10.
_OPENREVIEW_FORUM_ID_RE = re.compile(
    r"openreview\.net/(?:forum|pdf)\?id=(?P<forum_id>[A-Za-z0-9]{10})"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# A number sitting directly after a conference designator is that conference's
# year (AAAI-26, AAAI 2026, IAAI-2027), never a submission number.
_CONFERENCE_PREFIX_RE = re.compile(r"(?:AAAI|IAAI|EAAI)[-\s]*$", re.IGNORECASE)
# Plausible conference years. Only consulted when a filler word separated the
# cue from the number, so a genuine 4-digit submission number in this range
# (they exist) still survives when the cue introduces it directly.
_CONFERENCE_YEAR_MIN = 2020
_CONFERENCE_YEAR_MAX = 2035

# A subject the sender is replying to or forwarding, rather than one they wrote.
_REPLY_MARKER_RE = re.compile(r"^\s*(?:re|fwd|fw|aw|tr)\s*:", re.IGNORECASE)
# Phrases that mark such a subject as a CONFERENCE NOTIFICATION. Mined from the
# real corpus (frequency among reply-subjects carrying a number, n=430):
# "paper number" 183 · "notification for your" 67 · "desk rejection" 61 ·
# "decision notification" 61 · "desk-reject" 35 · "official review" 28 ·
# "assigned paper" 26 · "update on your" 12 · "review posted" 9 ·
# "restored by venue" 7.
# "paper number" is DELIBERATELY EXCLUDED despite being the most frequent: it is
# an identifier label, not a notification marker — a sender may write it in their
# own subject, and including it would fire the exception on ~43% of replies,
# which is no longer a narrow exception.
_NOTIFICATION_PHRASE_RE = re.compile(
    r"decision notification|notification for your|desk[-\s]?reject"
    r"|official review|review posted|assigned paper|update on your"
    r"|restored by venue",
    re.IGNORECASE,
)


def _reads_as_conference_year(value: str) -> bool:
    return len(value) == 4 and _CONFERENCE_YEAR_MIN <= int(value) <= _CONFERENCE_YEAR_MAX


def _accept_submission_match(match: re.Match[str], text: str) -> bool:
    """Reject the two false-positive shapes real ticket traffic produces."""
    if _CONFERENCE_PREFIX_RE.search(text[: match.start("number")]):
        return False
    filler = (match.groupdict().get("filler") or "").strip()
    return not (filler and _reads_as_conference_year(match.group("number")))


def _first_cue_number(text: str) -> str | None:
    """First accepted CUE-WORDED number in ``text`` (the bare ``#`` form aside)."""
    for match in _SUBMISSION_CUE_RE.finditer(text):
        if _accept_submission_match(match, text):
            return match.group("number")
    return None


def _is_quoted_notification_subject(subject: str) -> bool:
    """Is this subject a conference notification the sender replied to/forwarded?

    Requires BOTH a reply/forward marker and a notification phrase, because
    either alone is far too common: plenty of ordinary replies open with "Re:",
    and a sender may legitimately write these words themselves. Together they
    identify a subject line the CONFERENCE wrote, not the sender.
    """
    return bool(
        _REPLY_MARKER_RE.match(subject) and _NOTIFICATION_PHRASE_RE.search(subject)
    )


def _find_submission_number(subject: str, body: str) -> str | None:
    """First trustworthy submission number in subject, else body.

    Subject is searched FIRST because a number there usually came from the
    conference's own notification, which the sender quoted or replied to — a
    more reliable provenance than a number typed into prose.

    ONE narrow exception, and it is the same fact turned around: when the
    subject is a quoted notification, its number is what the conference wrote
    about back THEN, which is not necessarily what the sender is asking about
    NOW. So if such a subject and the body each yield a valid cue-worded number
    and they DISAGREE, the body wins — that is the current request. Observed in
    real traffic: a reply under an old decision-notification subject for one
    paper whose actual ask is to be unassigned from a different one.

    Deliberately narrow. It requires a quoted-notification subject AND a
    cue-worded body number AND the two to differ; miss any one and the original
    subject-first order runs unchanged. It is also cue-vs-cue only: the bare
    ``#NNNNN`` form never triggers it, so hash-vs-cue precedence is untouched.
    """
    subject = subject or ""
    body = body or ""

    subject_cue = _first_cue_number(subject)
    if subject_cue is not None and _is_quoted_notification_subject(subject):
        body_cue = _first_cue_number(body)
        if body_cue is not None and body_cue != subject_cue:
            return body_cue

    for text in (subject, body):
        for pattern in (_SUBMISSION_CUE_RE, _HASH_NUMBER_RE):
            for match in pattern.finditer(text):
                if _accept_submission_match(match, text):
                    return match.group("number")
    return None


def _find_openreview_forum_id(subject: str, body: str) -> str | None:
    for text in (subject or "", body or ""):
        match = _OPENREVIEW_FORUM_ID_RE.search(text)
        if match is not None:
            return match.group("forum_id")
    return None


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
    """Which submissions an email refers to, and who it names.

    Every field is a LIST: an email may legitimately name several submissions
    (an appeal covering two desk rejections, a reviewer asking to be unassigned
    from four papers), and reporting only one silently discarded the rest.

    ``method`` records HOW the values were obtained, not how well: an
    ``llm_distiller`` result with everything empty means the model looked and
    found nothing, which is a real finding and distinct from ``none`` (nothing
    looked at all). That distinction lives in ``method`` and in whether the
    whole result is stored at all — an EMPTY LIST means "examined, found none",
    never "not examined".
    """

    submission_numbers: list[str] = Field(
        default_factory=list,
        description="Submission/paper numbers as identified, deduplicated, in "
        "first-seen order. Empty when the email named none.",
    )
    openreview_forum_ids: list[str] = Field(
        default_factory=list,
        description="OpenReview forum ids as identified, deduplicated, in "
        "first-seen order. Empty when the email named none.",
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


def _dedupe_identifiers(values: list[str]) -> list[str]:
    """Strip blanks and exact repeats from raw identifiers, first-seen order.

    EXACT match, deliberately — not the ``casefold`` used for author names.
    These are opaque tokens rather than free text, so there is no casing or
    spacing variation to fold, and folding case would be actively WRONG for an
    OpenReview forum id: ``Ab3xY9kLm2`` and ``ab3xy9klm2`` are different ids.

    Blanks are dropped defensively. The distiller already filters them, but a
    model's output shape is never fully trusted here, and one stray empty line
    would otherwise render as a blank row in the panel.
    """
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique


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

        ``subject`` / ``body`` / ``sender`` / ``sender_name`` feed the regex
        fallback only. On the LLM path they are unused on purpose — the model
        already read the subject and body itself.

        When ``distilled`` is present it is trusted outright, INCLUDING when
        ALL THREE of its lists are empty: the prompt directs the model to read
        both subject and body and to emit no line when there is genuinely no
        identifier, so an empty LLM result is an answer, not a failure to
        re-litigate with a strictly weaker tool. It stays ``llm_distiller``
        with empty lists rather than falling through to regex.
        """
        try:
            if distilled is None:
                return self._extract_by_regex(subject, body, sender, sender_name)

            authors = [
                mention
                for raw in distilled.authors_raw
                if (mention := _parse_author(raw)) is not None
            ]
            return ExtractionResult(
                submission_numbers=_dedupe_identifiers(
                    distilled.submission_numbers_raw
                ),
                openreview_forum_ids=_dedupe_identifiers(
                    distilled.openreview_ids_raw
                ),
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

    def _extract_by_regex(
        self,
        subject: str,
        body: str,
        sender: str,
        sender_name: str | None,
    ) -> ExtractionResult:
        """Fallback used only when the distiller did not run.

        The author here is the SENDER, constructed directly rather than parsed:
        the envelope already gives a clean name and address, so none of the
        malformed-string salvage the LLM path needs applies. Signature-block
        parsing is deliberately out of scope — co-authors named in the body are
        exactly what the LLM path is for.

        The "usable" bar for ``method``: at least one populated field, i.e. a
        submission number, a forum id, or a sender mention with a name or an
        address. ``none`` is therefore reserved for input carrying no
        identifying trace whatsoever — in practice a malformed or synthetic
        ingest, since a real email always has a sender. That is the intended
        reading: ``none`` means "nothing to go on", not "regex found no
        number", which is an ordinary outcome recorded as ``regex_fallback``
        with an empty ``submission_number``.
        """
        submission_number = _find_submission_number(subject, body)
        forum_id = _find_openreview_forum_id(subject, body)

        authors: list[AuthorMention] = []
        name = _field_or_none(sender_name or "")
        email = _field_or_none(sender or "")
        if name is not None or email is not None:
            authors.append(AuthorMention(name=name, email=email))

        found_anything = bool(submission_number or forum_id or authors)
        # CONSTRUCTION SITE ONLY — adapted to the widened list fields so the
        # module stays coherent. The finding logic above is untouched and still
        # returns AT MOST ONE of each, so these lists hold 0 or 1 element.
        # Widening the regex path to collect every match is the next piece.
        return ExtractionResult(
            submission_numbers=[submission_number] if submission_number else [],
            openreview_forum_ids=[forum_id] if forum_id else [],
            authors=authors,
            method="regex_fallback" if found_anything else "none",
        )
