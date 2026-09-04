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

# --- OpenReview note id (one comment inside a forum) ------------------------
# A forum link may name the specific note under discussion in a `noteId` query
# parameter, as in openreview.net/forum?id=ll0avn6ylq&noteId=jnHgRMHgrm.
#
# A note id is meaningful ONLY alongside the forum id it travelled with, so it
# must never be matched on its own: a forum id taken from one link and a noteId
# taken from another would together name a comment that does not exist in that
# forum, and would look exactly like a real pair. The link is therefore matched
# WHOLE and its query string read as a single unit; the two parameter patterns
# below are only ever applied to one such query at a time.
#
# The forum-id pattern above is deliberately NOT reused or modified here — this
# is a strictly additive scan, and `openreview_forum_ids` keeps precisely the
# behaviour it had.
_OPENREVIEW_LINK_RE = re.compile(
    r"openreview\.net/(?:forum|pdf)\?(?P<query>[^\s\"'<>]*)",
    re.IGNORECASE,
)
# Anchoring each parameter to a separator (start-of-query, `&`, or an
# HTML-escaped `&amp;`) is load-bearing twice over. It stops `id=` matching the
# tail of `noteId=` — under IGNORECASE those three characters are identical —
# and it lets EITHER parameter come first, so both query orderings parse.
_OPENREVIEW_PARAM_FORUM_ID_RE = re.compile(
    r"(?:^|&amp;|&)id=(?P<forum_id>[A-Za-z0-9]{10})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# The value shape is looser than the forum id's fixed 10 on purpose: OpenReview
# API v1 note ids are numeric while v2 ids are 10-character tokens. A named
# `noteId` parameter inside an already-validated forum link is specific enough
# that a permissive value carries no false-positive risk — the precision comes
# from the surrounding link, not from the token. The trailing lookahead still
# rejects an over-long token outright rather than truncating it into a match.
_OPENREVIEW_PARAM_NOTE_ID_RE = re.compile(
    r"(?:^|&amp;|&)noteId=(?P<note_id>[A-Za-z0-9]{1,32})(?![A-Za-z0-9])",
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

# NOTE: the quoted-notification tie-break (a reply-marker regex plus a mined
# list of AAAI notification phrases) lived here and has been REMOVED, not
# disabled. It existed solely to pick between a subject number and a
# conflicting body number; now that both are reported there is nothing to pick,
# so it was dead weight rather than a guard. Its acceptance logic was never
# involved — that lives in _accept_submission_match below and is untouched.


def _reads_as_conference_year(value: str) -> bool:
    return len(value) == 4 and _CONFERENCE_YEAR_MIN <= int(value) <= _CONFERENCE_YEAR_MAX


def _accept_submission_match(match: re.Match[str], text: str) -> bool:
    """Reject the two false-positive shapes real ticket traffic produces."""
    if _CONFERENCE_PREFIX_RE.search(text[: match.start("number")]):
        return False
    filler = (match.groupdict().get("filler") or "").strip()
    return not (filler and _reads_as_conference_year(match.group("number")))


def _find_submission_numbers(subject: str, body: str) -> list[str]:
    """EVERY trustworthy submission number, subject first, deduplicated.

    Scanning no longer stops at the first hit: an email may name several
    submissions, and returning one silently discarded the rest. WHAT counts as
    a match is unchanged — every candidate still passes
    :func:`_accept_submission_match`, so the cue-word gate, the
    conference-designator rejection and the year rule all still apply.

    Order is the existing traversal, kept deliberately: subject before body
    (discovery found the subject usually carries the conference's own,
    highest-value id), and within each, cue-worded matches before the bare
    ``#NNNNN`` form (the stronger signal first). That was the old precedence
    chain; it now decides list ORDER rather than which single value survives.

    Note this replaces the quoted-notification tie-break outright. That existed
    only to choose between a subject number and a conflicting body number —
    with both reported there is nothing left to disambiguate.
    """
    found: list[str] = []
    for text in (subject or "", body or ""):
        for pattern in (_SUBMISSION_CUE_RE, _HASH_NUMBER_RE):
            for match in pattern.finditer(text):
                if _accept_submission_match(match, text):
                    found.append(match.group("number"))
    return _dedupe_identifiers(found)


def _find_openreview_forum_ids(subject: str, body: str) -> list[str]:
    """EVERY valid forum id, subject first, deduplicated.

    Dedup is case-SENSITIVE (via :func:`_dedupe_identifiers`) because forum ids
    are case-sensitive tokens — two ids differing only in case are two
    different papers.
    """
    found: list[str] = []
    for text in (subject or "", body or ""):
        for match in _OPENREVIEW_FORUM_ID_RE.finditer(text):
            found.append(match.group("forum_id"))
    return _dedupe_identifiers(found)


def _find_openreview_note_pairs(subject: str, body: str) -> list[tuple[str, str]]:
    """Every ``(forum_id, note_id)`` read from a SINGLE link's query string.

    Pairing is the whole point. Each link is matched whole and its query read
    once, so the two values in a pair always came from the same URL; a forum id
    in one link and a ``noteId`` in another are never combined. A link carrying
    only one of the two contributes nothing — a note id with no forum beside it
    names a comment we cannot place, and a forum id with no note is already the
    job of :func:`_find_openreview_forum_ids`.

    Either parameter ordering parses (``?id=...&noteId=...`` and
    ``?noteId=...&id=...``); see the pattern comments for why that is anchored
    rather than assumed. Order of results is the existing traversal, subject
    before body. Not deduplicated: the caller takes the first usable pair.
    """
    pairs: list[tuple[str, str]] = []
    for text in (subject or "", body or ""):
        for link in _OPENREVIEW_LINK_RE.finditer(text):
            query = link.group("query")
            forum = _OPENREVIEW_PARAM_FORUM_ID_RE.search(query)
            note = _OPENREVIEW_PARAM_NOTE_ID_RE.search(query)
            if forum is not None and note is not None:
                pairs.append((forum.group("forum_id"), note.group("note_id")))
    return pairs


def _first_note_id_for(
    pairs: list[tuple[str, str]], forum_ids: list[str]
) -> str | None:
    """The first note id whose forum id this result actually reports.

    A coherence gate, not a second filter on validity. It keeps the scalar
    consistent with the list beside it: a note id whose forum is missing from
    ``openreview_forum_ids`` would point into a discussion the result never
    mentions, which reads as a bug to anything downstream.

    Concretely it suppresses the ``?noteId=...&id=...`` ordering today. The pair
    scan above reads that shape, but the untouched forum-id pattern is anchored
    to a literal ``?id=`` and does not, so the forum id is absent from the list
    and the note id is withheld rather than orphaned. That asymmetry is a
    consequence of leaving the existing pattern exactly as it was, and it is
    self-correcting: widen that pattern and this gate opens with it, no second
    edit needed.
    """
    reported = set(forum_ids)
    for forum_id, note_id in pairs:
        if forum_id in reported:
            return note_id
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

    The identifier and author fields are LISTS: an email may legitimately name
    several submissions (an appeal covering two desk rejections, a reviewer
    asking to be unassigned from four papers), and reporting only one silently
    discarded the rest.

    ``openreview_note_id`` is the deliberate exception and is a SCALAR. It names
    one comment inside one forum, so unlike a submission reference it is only
    meaningful attached to a single forum id — a set of note ids with no record
    of which forum each belongs to would not be usable.

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
    openreview_note_id: str | None = Field(
        default=None,
        description="OpenReview note (Official Comment) id, when a forum link "
        "carried one as its `noteId` parameter. Read from the SAME link as its "
        "forum id and never paired across links, so it always names a comment "
        "inside a forum this result also reports. None when no link carried "
        "one — which, like an empty list, means 'looked, found none' whenever "
        "`method` is not 'none'.",
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
                # Always None here, set explicitly rather than left to the
                # default so the omission reads as a decision and not an
                # oversight. The distiller's output contract carries no note-id
                # line at all, and its OPENREVIEW_ID line reports a BARE id with
                # the link it came from already discarded — so there is nothing
                # on this path to pair a note id WITH, and that pairing is the
                # entire correctness property of this field.
                #
                # Re-reading the raw body with the regex to fill it in is NOT
                # the fix: it would make one result part-LLM and part-regex,
                # which `method` has no way to express (see the module
                # docstring — the two paths are mutually exclusive by design,
                # and merging them needs per-field provenance first).
                # Populating this on the LLM path therefore means teaching the
                # distiller to emit the pair itself, which is a change to its
                # prompt contract and belongs in its own commit.
                openreview_note_id=None,
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

        The "usable" bar for ``method``, restated for lists: at least one
        populated field, i.e. a NON-EMPTY submission-number list, OR a non-empty
        forum-id list, OR a sender mention with a name or an address. Same rule
        as before, with "non-empty list" where "non-None scalar" used to be.
        ``none`` is therefore still reserved for input carrying no identifying
        trace whatsoever — in practice a malformed or synthetic ingest, since a
        real email always has a sender. That is the intended reading: ``none``
        means "nothing to go on", not "regex found no number", which is an
        ordinary outcome recorded as ``regex_fallback`` with empty lists.
        """
        submission_numbers = _find_submission_numbers(subject, body)
        forum_ids = _find_openreview_forum_ids(subject, body)
        # Gated on `forum_ids` so the scalar can never name a forum the list
        # omits. Deliberately NOT part of `found_anything` below: a note id only
        # ever accompanies a forum id that already counted, so including it
        # could not change the outcome, and adding it would imply it can.
        note_id = _first_note_id_for(
            _find_openreview_note_pairs(subject, body), forum_ids
        )

        authors: list[AuthorMention] = []
        name = _field_or_none(sender_name or "")
        email = _field_or_none(sender or "")
        if name is not None or email is not None:
            authors.append(AuthorMention(name=name, email=email))

        found_anything = bool(submission_numbers or forum_ids or authors)
        return ExtractionResult(
            submission_numbers=submission_numbers,
            openreview_forum_ids=forum_ids,
            openreview_note_id=note_id,
            authors=authors,
            method="regex_fallback" if found_anything else "none",
        )
