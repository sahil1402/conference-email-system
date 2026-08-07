"""Tests for the paper/author extractor (LLM path).

SCOPE LIMIT: subtask 2a implements only the branch where the distiller ran.
Everything here drives ``extract`` through a constructed ``DistillResult`` — no
model call, no HTTP, no regex. The ``distilled is None`` branch is asserted only
to pin its current stub contract (``method="none"``); the regex fallback that
will replace it is subtask 2b.
"""

from app.pipeline.distiller import DistillResult
from app.pipeline.extractor import (
    AuthorMention,
    EmailExtractor,
    ExtractionResult,
    _dedupe_authors,
    _parse_author,
)

_SUBJECT = "Re: Regarding the Desk Rejection of Your AAAI-2026 Submission 12345"
_BODY = "Dear Chairs, we are writing about our submission."
_SENDER = "jane@example.edu"
_SENDER_NAME = "Jane Roe"


def _distilled(**kwargs) -> DistillResult:
    """A DistillResult with the pipeline-critical fields already filled in."""
    return DistillResult(
        queries=["desk rejection appeal procedure"],
        intent="desk_reject_appeal",
        confidence=0.9,
        **kwargs,
    )


def _extract(distilled: DistillResult | None) -> ExtractionResult:
    return EmailExtractor().extract(
        _SUBJECT, _BODY, _SENDER, _SENDER_NAME, distilled
    )


# ---------------------------------------------------------------------------
# Full and partial data
# ---------------------------------------------------------------------------
def test_extract_full_data():
    result = _extract(
        _distilled(
            submission_number_raw="12345",
            openreview_id_raw="Ab3xY9kLm2",
            authors_raw=[
                "Jane Roe | jane@example.edu | Example University",
                "John Doe | john@example.org | Other Institute",
            ],
        )
    )
    assert result.method == "llm_distiller"
    assert result.submission_number == "12345"
    assert result.openreview_forum_id == "Ab3xY9kLm2"
    assert result.authors == [
        AuthorMention(
            name="Jane Roe",
            email="jane@example.edu",
            affiliation="Example University",
        ),
        AuthorMention(
            name="John Doe",
            email="john@example.org",
            affiliation="Other Institute",
        ),
    ]


def test_extract_partial_author_fields_kept_as_partial_mention():
    """A NONE piece nulls only that field — it never drops the whole entry."""
    result = _extract(
        _distilled(authors_raw=["Jane Roe | NONE | Example University"])
    )
    assert result.authors == [
        AuthorMention(
            name="Jane Roe", email=None, affiliation="Example University"
        )
    ]


def test_extract_none_sentinel_is_case_insensitive_per_field():
    result = _extract(_distilled(authors_raw=["Jane Roe | none | None"]))
    assert result.authors == [
        AuthorMention(name="Jane Roe", email=None, affiliation=None)
    ]


def test_extract_submission_number_and_forum_id_pass_through_independently():
    """Either identifier may be present without the other."""
    only_number = _extract(_distilled(submission_number_raw="12345"))
    assert only_number.submission_number == "12345"
    assert only_number.openreview_forum_id is None

    only_forum = _extract(_distilled(openreview_id_raw="Ab3xY9kLm2"))
    assert only_forum.submission_number is None
    assert only_forum.openreview_forum_id == "Ab3xY9kLm2"


def test_extract_does_not_revalidate_identifier_shape():
    """Pass-through is verbatim: shape enforcement is the prompt's job, and a
    value the model insisted on must stay visible rather than be silently
    nulled here."""
    result = _extract(
        _distilled(submission_number_raw="AAAI-2026", openreview_id_raw="short")
    )
    assert result.submission_number == "AAAI-2026"
    assert result.openreview_forum_id == "short"


# ---------------------------------------------------------------------------
# Malformed author strings — best-effort parse, never skipped
# ---------------------------------------------------------------------------
def test_extract_author_missing_separators_parsed_as_name_only():
    """A bare name means the same thing as `John Doe | NONE | NONE`."""
    result = _extract(_distilled(authors_raw=["John Doe"]))
    assert result.authors == [
        AuthorMention(name="John Doe", email=None, affiliation=None)
    ]


def test_extract_author_with_one_separator_fills_name_and_email():
    result = _extract(_distilled(authors_raw=["Ann Poe | ann@example.edu"]))
    assert result.authors == [
        AuthorMention(name="Ann Poe", email="ann@example.edu", affiliation=None)
    ]


def test_extract_author_with_extra_separators_rejoins_affiliation():
    """A surplus separator is salvaged into the affiliation, not truncated."""
    result = _extract(
        _distilled(authors_raw=["Jane Roe | jane@example.edu | Dept | Example U"])
    )
    assert result.authors == [
        AuthorMention(
            name="Jane Roe",
            email="jane@example.edu",
            affiliation="Dept | Example U",
        )
    ]


def test_extract_author_with_no_populated_field_is_dropped():
    """`| |` and `NONE | NONE | NONE` identify nobody, so they are not mentions."""
    result = _extract(_distilled(authors_raw=["| |", "NONE | NONE | NONE"]))
    assert result.authors == []


def test_extract_malformed_author_does_not_discard_its_siblings():
    """One bad entry must not cost the good ones."""
    result = _extract(
        _distilled(
            authors_raw=[
                "Jane Roe | jane@example.edu | Example University",
                "| |",
                "John Doe",
            ]
        )
    )
    assert [a.name for a in result.authors] == ["Jane Roe", "John Doe"]


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------
def test_extract_dedupes_authors_case_insensitively():
    result = _extract(
        _distilled(
            authors_raw=[
                "Jane Roe | jane@example.edu | Example University",
                "JANE ROE | JANE@EXAMPLE.EDU | Example University",
            ]
        )
    )
    assert len(result.authors) == 1
    assert result.authors[0].name == "Jane Roe"  # first-seen wins


def test_extract_dedupe_keeps_first_seen_order():
    result = _extract(
        _distilled(
            authors_raw=[
                "Jane Roe | jane@example.edu | NONE",
                "John Doe | john@example.org | NONE",
                "jane roe | jane@example.edu | NONE",
            ]
        )
    )
    assert [a.name for a in result.authors] == ["Jane Roe", "John Doe"]


def test_extract_dedupe_ignores_affiliation_differences():
    """Same person, one entry with an institution and one without, is one person."""
    result = _extract(
        _distilled(
            authors_raw=[
                "Jane Roe | jane@example.edu | Example University",
                "Jane Roe | jane@example.edu | NONE",
            ]
        )
    )
    assert len(result.authors) == 1
    assert result.authors[0].affiliation == "Example University"


def test_extract_dedupe_keeps_distinct_people_apart():
    """Same affiliation must not collapse two different people."""
    result = _extract(
        _distilled(
            authors_raw=[
                "Jane Roe | jane@example.edu | Example University",
                "John Doe | john@example.org | Example University",
            ]
        )
    )
    assert len(result.authors) == 2


def test_extract_dedupe_uses_casefold_for_non_ascii_names():
    """Real traffic carries non-ASCII names; `lower()` under-folds some of them."""
    assert len(_dedupe_authors([
        AuthorMention(name="STRASSE", email=None, affiliation=None),
        AuthorMention(name="strasse", email=None, affiliation=None),
    ])) == 1
    # German sharp-s: casefold maps "ß" to "ss", lower() does not.
    assert len(_dedupe_authors([
        AuthorMention(name="STRASSE", email=None, affiliation=None),
        AuthorMention(name="straße", email=None, affiliation=None),
    ])) == 1


def test_extract_same_name_different_email_are_distinct_mentions():
    """The key is (name, email) — a differing email is a differing mention."""
    result = _extract(
        _distilled(
            authors_raw=[
                "Jane Roe | jane@example.edu | NONE",
                "Jane Roe | jane.roe@other.org | NONE",
            ]
        )
    )
    assert len(result.authors) == 2


# ---------------------------------------------------------------------------
# Empty / absent input
# ---------------------------------------------------------------------------
def test_extract_empty_authors_raw():
    result = _extract(_distilled(submission_number_raw="12345"))
    assert result.authors == []
    assert result.method == "llm_distiller"


def test_extract_distilled_with_all_fields_empty_is_still_llm_distiller():
    """Point-3 contract: the model ran and found nothing.

    That is an ANSWER, not a failure — the prompt directs it to read both
    subject and body — so it must not be recorded as `none` (nothing looked)
    and, once subtask 2b lands, must not fall through to the regex path.
    """
    result = _extract(_distilled())
    assert result.method == "llm_distiller"
    assert result.submission_number is None
    assert result.openreview_forum_id is None
    assert result.authors == []


def test_extract_without_distilled_routes_to_the_regex_fallback():
    """The absence of a distiller result is what selects the regex path.

    (Replaces the 2a stub assertion, which pinned `method="none"` only because
    the fallback did not exist yet.)
    """
    result = _extract(None)
    assert result.method == "regex_fallback"
    # `_SUBJECT` carries a cue-worded number, so the fallback really ran.
    assert result.submission_number == "12345"


def test_extract_with_distilled_never_consults_the_regex_fallback():
    """Locked decision: regex is a fallback, never a supplement.

    The subject here carries a number the regex would happily find; a present
    distiller result must suppress it entirely rather than top itself up.
    """
    result = _extract(_distilled())
    assert result.method == "llm_distiller"
    assert result.submission_number is None
    assert result.openreview_forum_id is None
    assert result.authors == []


# ---------------------------------------------------------------------------
# Failure policy
# ---------------------------------------------------------------------------
def test_extract_never_raises_on_bad_input():
    """A normalization bug degrades to an empty result, never an exception.

    Identification is an enrichment; it must never break the drafting pipeline
    that calls it.
    """

    class _Exploding:
        queries = ["q"]
        submission_number_raw = "12345"
        openreview_id_raw = None

        @property
        def authors_raw(self):
            raise RuntimeError("boom")

    result = EmailExtractor().extract(
        _SUBJECT, _BODY, _SENDER, _SENDER_NAME, _Exploding()
    )
    assert result.method == "none"
    assert result.authors == []


# ---------------------------------------------------------------------------
# Model defaults
# ---------------------------------------------------------------------------
def test_extraction_result_defaults_are_empty():
    result = ExtractionResult()
    assert result.submission_number is None
    assert result.openreview_forum_id is None
    assert result.authors == []
    assert result.method == "none"


def test_author_mention_defaults_are_empty():
    mention = AuthorMention()
    assert mention.name is None
    assert mention.email is None
    assert mention.affiliation is None


def test_parse_author_returns_none_for_empty_entry():
    assert _parse_author("NONE | NONE | NONE") is None
    assert _parse_author("  ") is None


# ---------------------------------------------------------------------------
# Regex fallback (subtask 2b) — reached ONLY when distilled is None
#
# SCOPE LIMIT: every example below is synthetic. Real ticket text is PII and
# never enters this suite; the shapes are modelled on the discovery findings
# (cue-word gating, the AAAI-designator and year false positives, the
# /group?id= false positive) without reproducing any real ticket's content.
# ---------------------------------------------------------------------------
def _regex_extract(
    subject: str = "", body: str = "", sender: str = "", sender_name: str | None = None
) -> ExtractionResult:
    """Drive the fallback by passing distilled=None."""
    return EmailExtractor().extract(subject, body, sender, sender_name, None)


# --- submission number -----------------------------------------------------
def test_regex_submission_number_with_cue_word():
    result = _regex_extract(body="We are writing about submission 12345.")
    assert result.submission_number == "12345"
    assert result.method == "regex_fallback"


def test_regex_submission_number_accepts_common_cue_shapes():
    for text, expected in [
        ("Paper ID: 26681", "26681"),
        ("Paper ID 26681", "26681"),
        ("paper 9904", "9904"),
        ("submission #10824", "10824"),
        ("Submission ID:7377", "7377"),
        ("Paper number: 29546", "29546"),
        ("our submissions 13276 and others", "13276"),
        ("regarding #18898", "18898"),
    ]:
        assert _regex_extract(body=text).submission_number == expected, text


def test_regex_submission_number_requires_a_cue_word():
    """A bare number with no cue is not a submission number."""
    assert _regex_extract(body="We waited 12345 seconds.").submission_number is None


def test_regex_submission_number_rejects_conference_designator_year():
    """`AAAI-26` / `AAAI 2026` is the conference, never the submission."""
    for text in [
        "our submission AAAI 2026 was rejected",
        "our submission AAAI-2026 was rejected",
        "the paper IAAI 2027 track",
    ]:
        assert _regex_extract(body=text).submission_number is None, text


def test_regex_submission_number_rejects_any_number_directly_after_designator():
    """A number adjacent to the designator is distrusted whatever its value.

    Deliberately covers a 5-digit number, which the year rule ignores — so this
    isolates the designator guard. In real traffic a designator is followed by
    its year, never by the submission number (that follows a cue like
    `Submission` / `Paper ID`), so the adjacency itself is the signal.
    """
    assert _regex_extract(body="our submission AAAI 12345 here").submission_number is None
    assert _regex_extract(body="the paper EAAI-31337 track").submission_number is None


def test_regex_submission_number_keeps_scanning_past_a_rejected_match():
    """A rejected candidate must not abort the search for a real one."""
    result = _regex_extract(body="our submission AAAI 12345 concerns paper 22336")
    assert result.submission_number == "22336"


def test_regex_submission_number_rejects_year_reached_across_a_word():
    """`paper due 2026` is a deadline; the cue does not introduce the number."""
    for text in ["the paper due 2026", "our submission deadline 2026"]:
        assert _regex_extract(body=text).submission_number is None, text


def test_regex_submission_number_accepts_year_like_value_adjacent_to_cue():
    """Genuine 4-digit numbers in the year range exist; a direct cue believes them."""
    assert _regex_extract(body="Submission 2026 was desk rejected.").submission_number == "2026"


def test_regex_submission_number_conference_year_not_taken_from_surrounding_text():
    """The designator year must lose to the real number later in the line."""
    result = _regex_extract(subject="Update on your AAAI 2026 Submission 22336")
    assert result.submission_number == "22336"


def test_regex_submission_number_found_in_subject_only():
    result = _regex_extract(
        subject="Re: Desk Rejection of Your Submission 15357",
        body="Dear chairs, please reconsider.",
    )
    assert result.submission_number == "15357"


def test_regex_submission_number_found_in_body_only():
    result = _regex_extract(
        subject="Appeal request", body="This concerns submission 15357."
    )
    assert result.submission_number == "15357"


def test_regex_submission_number_prefers_subject_over_body():
    """Subject numbers usually come from the conference's own notification."""
    result = _regex_extract(
        subject="Re: Your Submission 11111", body="Also see submission 22222."
    )
    assert result.submission_number == "11111"


def test_regex_submission_number_rejects_wrong_length():
    """4-5 digits only: shorter is a count, longer is not a submission number."""
    assert _regex_extract(body="paper 123").submission_number is None
    assert _regex_extract(body="paper 123456").submission_number is None


# --- OpenReview forum id ---------------------------------------------------
def test_regex_forum_id_from_forum_link():
    result = _regex_extract(body="See https://openreview.net/forum?id=Ab3xY9kLm2 please.")
    assert result.openreview_forum_id == "Ab3xY9kLm2"
    assert result.method == "regex_fallback"


def test_regex_forum_id_from_pdf_link():
    result = _regex_extract(body="PDF: https://openreview.net/pdf?id=Zz9QwErTy1")
    assert result.openreview_forum_id == "Zz9QwErTy1"


def test_regex_forum_id_rejects_group_link():
    """AAAI committee/group URLs share the ?id= shape — confirmed false positive."""
    result = _regex_extract(
        body="Join https://openreview.net/group?id=AAAI.org/2026/Conference today."
    )
    assert result.openreview_forum_id is None


def test_regex_forum_id_rejects_bare_id_parameter():
    """A ?id= on any other host or path is not a forum id."""
    for text in [
        "https://example.com/thing?id=Ab3xY9kLm2",
        "https://openreview.net/profile?id=Ab3xY9kLm2",
        "see ?id=Ab3xY9kLm2",
    ]:
        assert _regex_extract(body=text).openreview_forum_id is None, text


def test_regex_forum_id_rejects_wrong_length():
    """Exactly 10 chars — a longer id must not be truncated into a false match."""
    assert _regex_extract(
        body="https://openreview.net/forum?id=Ab3xY9kLm2Extra24Chars"
    ).openreview_forum_id is None
    assert _regex_extract(
        body="https://openreview.net/forum?id=Short1"
    ).openreview_forum_id is None


def test_regex_forum_id_found_in_subject():
    result = _regex_extract(subject="Re: openreview.net/forum?id=Ab3xY9kLm2")
    assert result.openreview_forum_id == "Ab3xY9kLm2"


def test_regex_forum_id_preserves_case():
    """The id is opaque and case-sensitive; matching must not normalize it."""
    assert (
        _regex_extract(body="openreview.net/forum?id=aB3Xy9KlM2").openreview_forum_id
        == "aB3Xy9KlM2"
    )


# --- sender-based author ---------------------------------------------------
def test_regex_sender_becomes_the_only_author():
    result = _regex_extract(sender=_SENDER, sender_name=_SENDER_NAME)
    assert result.authors == [
        AuthorMention(name="Jane Roe", email="jane@example.edu", affiliation=None)
    ]


def test_regex_sender_only_still_counts_as_found():
    """No regex hit but a real sender is still a usable result, not `none`."""
    result = _regex_extract(
        body="Please advise on my situation.", sender=_SENDER, sender_name=_SENDER_NAME
    )
    assert result.method == "regex_fallback"
    assert result.submission_number is None
    assert len(result.authors) == 1


def test_regex_sender_email_without_name():
    result = _regex_extract(sender=_SENDER, sender_name=None)
    assert result.authors == [
        AuthorMention(name=None, email="jane@example.edu", affiliation=None)
    ]


def test_regex_sender_blank_fields_produce_no_author():
    result = _regex_extract(body="submission 12345", sender="   ", sender_name="  ")
    assert result.authors == []
    assert result.method == "regex_fallback"  # the number is still a find


def test_regex_never_invents_affiliation():
    """Signature-block parsing is out of scope — affiliation stays unset."""
    result = _regex_extract(
        body="Best regards,\nJane Roe\nExample University\nDept of CS",
        sender=_SENDER,
        sender_name=_SENDER_NAME,
    )
    assert result.authors[0].affiliation is None


# --- nothing found ---------------------------------------------------------
def test_regex_nothing_usable_is_method_none():
    """No sender and no regex hit: nothing to go on at all."""
    result = _regex_extract(subject="Question", body="Can you help?", sender="")
    assert result.method == "none"
    assert result.submission_number is None
    assert result.openreview_forum_id is None
    assert result.authors == []


def test_regex_empty_input_is_method_none():
    result = _regex_extract()
    assert result.method == "none"


def test_regex_fallback_finds_both_identifiers_together():
    result = _regex_extract(
        subject="Re: Your Submission 22336",
        body="Forum: https://openreview.net/forum?id=Ab3xY9kLm2",
        sender=_SENDER,
        sender_name=_SENDER_NAME,
    )
    assert result.submission_number == "22336"
    assert result.openreview_forum_id == "Ab3xY9kLm2"
    assert len(result.authors) == 1
    assert result.method == "regex_fallback"


def test_regex_fallback_never_raises_on_odd_input():
    result = EmailExtractor().extract("", "", "", None, None)
    assert result.method == "none"
