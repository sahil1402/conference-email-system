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
            submission_numbers_raw=["12345"],
            openreview_ids_raw=["Ab3xY9kLm2"],
            authors_raw=[
                "Jane Roe | jane@example.edu | Example University",
                "John Doe | john@example.org | Other Institute",
            ],
        )
    )
    assert result.method == "llm_distiller"
    assert result.submission_numbers == ["12345"]
    assert result.openreview_forum_ids == ["Ab3xY9kLm2"]
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
    only_number = _extract(_distilled(submission_numbers_raw=["12345"]))
    assert only_number.submission_numbers == ["12345"]
    assert only_number.openreview_forum_ids == []

    only_forum = _extract(_distilled(openreview_ids_raw=["Ab3xY9kLm2"]))
    assert only_forum.submission_numbers == []
    assert only_forum.openreview_forum_ids == ["Ab3xY9kLm2"]


def test_extract_does_not_revalidate_identifier_shape():
    """Pass-through is verbatim: shape enforcement is the prompt's job, and a
    value the model insisted on must stay visible rather than be silently
    dropped here."""
    result = _extract(
        _distilled(
            submission_numbers_raw=["AAAI-2026"], openreview_ids_raw=["short"]
        )
    )
    assert result.submission_numbers == ["AAAI-2026"]
    assert result.openreview_forum_ids == ["short"]


# ---------------------------------------------------------------------------
# Multiple identifiers — the point of the list shape
# ---------------------------------------------------------------------------
def test_extract_multiple_submission_numbers():
    """An appeal covering two desk rejections names both; both are reported."""
    result = _extract(_distilled(submission_numbers_raw=["11111", "22222"]))
    assert result.submission_numbers == ["11111", "22222"]


def test_extract_multiple_openreview_forum_ids():
    result = _extract(
        _distilled(openreview_ids_raw=["Ab3xY9kLm2", "Zz9QwErTy1"])
    )
    assert result.openreview_forum_ids == ["Ab3xY9kLm2", "Zz9QwErTy1"]


def test_extract_multiple_of_both_kinds_at_once():
    result = _extract(
        _distilled(
            submission_numbers_raw=["11111", "22222", "33333"],
            openreview_ids_raw=["Ab3xY9kLm2", "Zz9QwErTy1"],
        )
    )
    assert result.submission_numbers == ["11111", "22222", "33333"]
    assert result.openreview_forum_ids == ["Ab3xY9kLm2", "Zz9QwErTy1"]


def test_extract_preserves_the_order_the_model_reported():
    """Order is the model's; re-sorting would discard which it named first."""
    result = _extract(_distilled(submission_numbers_raw=["99999", "11111"]))
    assert result.submission_numbers == ["99999", "11111"]


def test_extract_dedupes_exact_duplicate_identifiers():
    """The distiller forwards duplicates verbatim; dedup happens here."""
    result = _extract(
        _distilled(
            submission_numbers_raw=["12345", "12345", "67890"],
            openreview_ids_raw=["Ab3xY9kLm2", "Ab3xY9kLm2"],
        )
    )
    assert result.submission_numbers == ["12345", "67890"]
    assert result.openreview_forum_ids == ["Ab3xY9kLm2"]


def test_extract_dedupe_keeps_the_first_occurrence():
    result = _extract(_distilled(submission_numbers_raw=["11111", "22222", "11111"]))
    assert result.submission_numbers == ["11111", "22222"]


def test_extract_forum_id_dedupe_is_case_SENSITIVE():
    """Casefolding would be WRONG here — forum ids are case-sensitive tokens,
    so two ids differing only in case are two different papers."""
    result = _extract(
        _distilled(openreview_ids_raw=["Ab3xY9kLm2", "ab3xy9klm2"])
    )
    assert result.openreview_forum_ids == ["Ab3xY9kLm2", "ab3xy9klm2"]


def test_extract_strips_blank_identifier_entries():
    """Defensive: the distiller filters blanks, but model shape is not trusted.

    An unfiltered blank would otherwise render as an empty row in the panel.
    """
    result = _extract(
        _distilled(
            submission_numbers_raw=["", "  ", "12345"],
            openreview_ids_raw=["   ", "Ab3xY9kLm2"],
        )
    )
    assert result.submission_numbers == ["12345"]
    assert result.openreview_forum_ids == ["Ab3xY9kLm2"]


def test_extract_trims_surrounding_whitespace_on_identifiers():
    result = _extract(_distilled(submission_numbers_raw=["  12345  "]))
    assert result.submission_numbers == ["12345"]


def test_extract_all_blank_identifiers_collapse_to_empty_lists():
    result = _extract(
        _distilled(submission_numbers_raw=["", " "], openreview_ids_raw=[""])
    )
    assert result.submission_numbers == []
    assert result.openreview_forum_ids == []
    # Still the model's answer, not a reason to fall through to regex.
    assert result.method == "llm_distiller"


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
    result = _extract(_distilled(submission_numbers_raw=["12345"]))
    assert result.authors == []
    assert result.method == "llm_distiller"


def test_extract_distilled_with_all_fields_empty_is_still_llm_distiller():
    """Point-3 contract: the model ran and found nothing.

    That is an ANSWER, not a failure — the prompt directs it to read both
    subject and body — so it must not be recorded as `none` (nothing looked)
    and must not fall through to the regex path. Restated for the list shape:
    ALL THREE lists empty is still the model's answer.
    """
    result = _extract(_distilled())
    assert result.method == "llm_distiller"
    assert result.submission_numbers == []
    assert result.openreview_forum_ids == []
    assert result.authors == []


def test_extract_without_distilled_routes_to_the_regex_fallback():
    """The absence of a distiller result is what selects the regex path.

    (Replaces the 2a stub assertion, which pinned `method="none"` only because
    the fallback did not exist yet.)
    """
    result = _extract(None)
    assert result.method == "regex_fallback"
    # `_SUBJECT` carries a cue-worded number, so the fallback really ran.
    # Single-element for now: the regex path still finds at most one of each
    # until it is widened in the next piece.
    assert result.submission_numbers == ["12345"]


def test_extract_with_distilled_never_consults_the_regex_fallback():
    """Locked decision: regex is a fallback, never a supplement.

    The subject here carries a number the regex would happily find; a present
    distiller result must suppress it entirely rather than top itself up.
    """
    result = _extract(_distilled())
    assert result.method == "llm_distiller"
    assert result.submission_numbers == []
    assert result.openreview_forum_ids == []
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
        submission_numbers_raw = ["12345"]
        openreview_ids_raw = []

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
    assert result.submission_numbers == []
    assert result.openreview_forum_ids == []
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


# --- quoted-notification subject vs. the body's current ask -----------------
# A reply under a conference notification's subject carries the number that
# notification was about, which is not necessarily what the sender is asking
# about now. Synthetic subjects modelled on the real shapes; no ticket text.
def test_regex_body_wins_when_quoted_notification_subject_disagrees():
    """THE defect: subject quotes an old notification for A, body asks about B."""
    result = _regex_extract(
        subject="Re: [AAAI-26] Decision notification for your submission 11111",
        body="Please remove me from paper 22222 — it is outside my area.",
    )
    assert result.submission_number == "22222"


def test_regex_quoted_notification_exception_covers_the_observed_phrasings():
    """Each mined notification phrase must trigger the exception."""
    for subject in [
        "Re: [AAAI-26] Decision notification for your submission 11111",
        "Re: Regarding the Desk Rejection of Your AAAI-2026 Submission 11111",
        "Re: [AAAI-26] Official Review posted to your assigned Paper 11111",
        "Fwd: Update on your AAAI 2026 Submission 11111",
        "Re: [AAAI-26]: Paper 11111 restored by venue organizers",
    ]:
        result = _regex_extract(
            subject=subject, body="This is about paper 22222 instead."
        )
        assert result.submission_number == "22222", subject


def test_regex_keeps_subject_number_when_body_has_none():
    """Nothing to conflict with — the subject's number stands."""
    result = _regex_extract(
        subject="Re: [AAAI-26] Decision notification for your submission 11111",
        body="Thank you for the update, I have no further questions.",
    )
    assert result.submission_number == "11111"


def test_regex_keeps_subject_number_when_body_reinforces_it():
    """The body repeating the SAME number is agreement, not a conflict."""
    result = _regex_extract(
        subject="Re: [AAAI-26] Decision notification for your submission 11111",
        body="I am writing about submission 11111 and would like to appeal.",
    )
    assert result.submission_number == "11111"


def test_regex_ordinary_subject_keeps_subject_first_precedence():
    """The exception must NOT fire on plain subject/body disagreement.

    A fresh, sender-written subject is not a quoted notification, so the
    original subject-first rule still governs.
    """
    result = _regex_extract(
        subject="Question about submission 11111",
        body="Also, what about paper 22222?",
    )
    assert result.submission_number == "11111"


def test_regex_reply_marker_alone_does_not_trigger_the_exception():
    """`Re:` without a notification phrase is just an ordinary reply."""
    result = _regex_extract(
        subject="Re: Question about submission 11111",
        body="Also, what about paper 22222?",
    )
    assert result.submission_number == "11111"


def test_regex_notification_phrase_alone_does_not_trigger_the_exception():
    """A sender writing the phrase in their OWN fresh subject is not a quote."""
    result = _regex_extract(
        subject="Desk rejection query for submission 11111",
        body="Also, what about paper 22222?",
    )
    assert result.submission_number == "11111"


def test_regex_reply_marker_must_be_at_the_START_of_the_subject():
    """The marker is anchored, and that anchoring is load-bearing.

    Unanchored, `re\\s*:` matches INSIDE ordinary words that happen to end in
    "re" before a colon — "Score:", "More:", "Failure:" — which would fire the
    exception on subjects that are not replies at all.
    """
    result = _regex_extract(
        subject="Score: 9 — desk rejection of submission 11111",
        body="Also see paper 22222.",
    )
    assert result.submission_number == "11111"


def test_regex_paper_number_label_alone_does_not_trigger_the_exception():
    """`Paper number:` is an id label, not a notification marker.

    It is the most frequent phrase in real reply-subjects, so treating it as a
    notification marker would fire the exception on a large share of ordinary
    replies — the opposite of a narrow exception.
    """
    result = _regex_extract(
        subject="Re: Paper number: 11111",
        body="Also, what about paper 22222?",
    )
    assert result.submission_number == "11111"


def test_regex_known_limitation_body_quoting_the_notification_defeats_the_fix():
    """KNOWN LIMITATION, pinned deliberately — not an assertion that this is right.

    When the body also QUOTES the notification, the body's first cue-worded
    number is that same quoted number, so no disagreement is detected and the
    subject's number stands even though the sender's real ask names a different
    paper further down.

    Measured on the real corpus, this is why the motivating ticket is not fixed
    by this change. The obvious widening — take the first body number that
    DIFFERS — was tried and rejected: it fixes that ticket but pulls numbers out
    of quoted foreign-conference notifications and cited evidence, a worse trade.
    Doing this properly needs quote-stripping (telling the sender's own prose
    apart from quoted blocks), which is a separate piece.
    """
    result = _regex_extract(
        subject="Re: [AAAI-26] Decision notification for your submission 11111",
        body=(
            "On Mon, AAAI wrote:\n"
            "> Subject: Decision notification for your submission 11111\n"
            "Please remove me from paper 22222 instead."
        ),
    )
    # Today: the quoted 11111 is seen first, so the exception does not fire.
    assert result.submission_number == "11111"


def test_regex_exception_is_cue_vs_cue_only():
    """A bare `#NNNNN` in the body must not displace the subject's number.

    Hash-vs-cue precedence is a separate, deliberate rule; this fix is only
    about two independently valid CUE-WORDED matches disagreeing.
    """
    result = _regex_extract(
        subject="Re: [AAAI-26] Decision notification for your submission 11111",
        body="See also #22222 for context.",
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
