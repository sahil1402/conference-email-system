"""Tests for the paper/author extractor — both paths.

The LLM path drives ``extract`` through a constructed ``DistillResult``; the
regex path drives it with ``distilled=None``. No model call, no HTTP.

Both paths now report LISTS: every submission number and forum id an email
names, not just the first. Regex examples are synthetic, modelled on shapes
found in the real corpus.
"""

from app.pipeline.distiller import DistillResult
from app.pipeline.extractor import (
    AuthorMention,
    EmailExtractor,
    ExtractionResult,
    _dedupe_authors,
    _find_openreview_note_pairs,
    _first_note_id_for,
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
    assert result.submission_numbers == ["12345"]
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
        assert _regex_extract(body=text).submission_numbers == [expected], text


def test_regex_submission_number_requires_a_cue_word():
    """A bare number with no cue is not a submission number."""
    assert _regex_extract(body="We waited 12345 seconds.").submission_numbers == []


def test_regex_submission_number_rejects_conference_designator_year():
    """`AAAI-26` / `AAAI 2026` is the conference, never the submission."""
    for text in [
        "our submission AAAI 2026 was rejected",
        "our submission AAAI-2026 was rejected",
        "the paper IAAI 2027 track",
    ]:
        assert _regex_extract(body=text).submission_numbers == [], text


def test_regex_submission_number_rejects_any_number_directly_after_designator():
    """A number adjacent to the designator is distrusted whatever its value.

    Deliberately covers a 5-digit number, which the year rule ignores — so this
    isolates the designator guard. In real traffic a designator is followed by
    its year, never by the submission number (that follows a cue like
    `Submission` / `Paper ID`), so the adjacency itself is the signal.
    """
    assert _regex_extract(body="our submission AAAI 12345 here").submission_numbers == []
    assert _regex_extract(body="the paper EAAI-31337 track").submission_numbers == []


def test_regex_submission_number_keeps_scanning_past_a_rejected_match():
    """A rejected candidate must not abort the search for a real one."""
    result = _regex_extract(body="our submission AAAI 12345 concerns paper 22336")
    assert result.submission_numbers == ["22336"]


def test_regex_submission_number_rejects_year_reached_across_a_word():
    """`paper due 2026` is a deadline; the cue does not introduce the number."""
    for text in ["the paper due 2026", "our submission deadline 2026"]:
        assert _regex_extract(body=text).submission_numbers == [], text


def test_regex_submission_number_accepts_year_like_value_adjacent_to_cue():
    """Genuine 4-digit numbers in the year range exist; a direct cue believes them."""
    assert _regex_extract(body="Submission 2026 was desk rejected.").submission_numbers == ["2026"]


def test_regex_submission_number_conference_year_not_taken_from_surrounding_text():
    """The designator year must lose to the real number later in the line."""
    result = _regex_extract(subject="Update on your AAAI 2026 Submission 22336")
    assert result.submission_numbers == ["22336"]


def test_regex_submission_number_found_in_subject_only():
    result = _regex_extract(
        subject="Re: Desk Rejection of Your Submission 15357",
        body="Dear chairs, please reconsider.",
    )
    assert result.submission_numbers == ["15357"]


def test_regex_submission_number_found_in_body_only():
    result = _regex_extract(
        subject="Appeal request", body="This concerns submission 15357."
    )
    assert result.submission_numbers == ["15357"]


# NOTE: `test_regex_submission_number_prefers_subject_over_body` lived here and
# is GONE, not adapted. It pinned that a subject number BEAT a body number —
# a claim that no longer exists now that both are reported. Its exact scenario
# is covered by `test_regex_collects_from_subject_AND_body_subject_first`
# below, which asserts the surviving property: subject-first ORDER.


# --- collecting EVERY match, not just the first -----------------------------
def test_regex_collects_two_numbers_each_with_its_own_cue():
    """Repeating the cue makes both numbers independently valid — both returned."""
    result = _regex_extract(body="See paper 11111 and paper 22222 for context.")
    assert result.submission_numbers == ["11111", "22222"]


def test_regex_collects_two_hash_numbers():
    """The bare `#` is its own cue, so each item in the list qualifies."""
    result = _regex_extract(body="Please reassign submissions #5458 and #21675.")
    assert result.submission_numbers == ["5458", "21675"]


def test_regex_collects_numbers_across_separate_sentences():
    result = _regex_extract(body="This is submission 11111. Also submission 22222.")
    assert result.submission_numbers == ["11111", "22222"]


def test_regex_collects_from_subject_AND_body_subject_first():
    """Replaces the old subject-precedence tie-break: both are reported now.

    There is no longer a winner to choose, so the subject/body disagreement the
    quoted-notification exception existed to arbitrate simply does not arise —
    which is why that exception (and its tests) are gone rather than adapted.
    """
    result = _regex_extract(
        subject="Re: Your Submission 11111", body="Please also see paper 22222."
    )
    assert result.submission_numbers == ["11111", "22222"]


def test_regex_dedupes_the_same_number_in_subject_and_body():
    result = _regex_extract(
        subject="Re: Your Submission 11111",
        body="I am writing about submission 11111 to appeal.",
    )
    assert result.submission_numbers == ["11111"]


def test_regex_keeps_rejecting_bad_candidates_alongside_good_ones():
    """Collecting all must not smuggle in candidates the guards reject."""
    result = _regex_extract(
        body="our submission AAAI 12345 concerns paper 22336 and paper due 2026"
    )
    # 12345 sits after a conference designator; 2026 is a year across a filler.
    assert result.submission_numbers == ["22336"]


def test_regex_collects_two_forum_ids_across_subject_and_body():
    result = _regex_extract(
        subject="Re: openreview.net/forum?id=Ab3xY9kLm2",
        body="and the other is https://openreview.net/pdf?id=Zz9QwErTy1",
    )
    assert result.openreview_forum_ids == ["Ab3xY9kLm2", "Zz9QwErTy1"]


def test_regex_dedupes_a_forum_id_repeated_in_the_body():
    result = _regex_extract(
        body=(
            "See https://openreview.net/forum?id=Ab3xY9kLm2 — again, "
            "https://openreview.net/forum?id=Ab3xY9kLm2"
        )
    )
    assert result.openreview_forum_ids == ["Ab3xY9kLm2"]


def test_regex_forum_id_collection_still_rejects_group_links():
    """A group link beside a real forum link must not be collected."""
    result = _regex_extract(
        body=(
            "Roster: https://openreview.net/group?id=AAAI.org/2026/Conference "
            "Paper: https://openreview.net/forum?id=Ab3xY9kLm2"
        )
    )
    assert result.openreview_forum_ids == ["Ab3xY9kLm2"]


def test_regex_collects_several_of_both_kinds_together():
    result = _regex_extract(
        subject="Re: Submissions #11111 and #22222",
        body=(
            "Forums: https://openreview.net/forum?id=Ab3xY9kLm2 and "
            "https://openreview.net/pdf?id=Zz9QwErTy1"
        ),
    )
    assert result.submission_numbers == ["11111", "22222"]
    assert result.openreview_forum_ids == ["Ab3xY9kLm2", "Zz9QwErTy1"]
    assert result.method == "regex_fallback"


def test_regex_known_limitation_bare_conjunction_second_item_still_missed():
    """KNOWN LIMITATION, pinned deliberately — not an assertion that this is right.

    "papers 11111 and 22222" yields only 11111. Collecting every match does NOT
    fix this: the second number is never a CANDIDATE, because the cue-word gate
    requires a cue adjacent to each number and "and 22222" carries none. That is
    an acceptance rule, not an early return, and acceptance was deliberately
    left untouched here.

    Measured on the real corpus: of the trailing items in "X and Y" / "X, Y"
    shapes, 76 are now captured (their second item carried its own cue, usually
    "#") and 62 remain missed like this one. Widening the cue gate to reach
    across a conjunction is a separate decision with its own false-positive
    risk.
    """
    assert _regex_extract(body="papers 11111 and 22222").submission_numbers == ["11111"]
    assert _regex_extract(body="Paper IDs 3157, 17066").submission_numbers == ["3157"]


def test_regex_submission_number_rejects_wrong_length():
    """4-5 digits only: shorter is a count, longer is not a submission number."""
    assert _regex_extract(body="paper 123").submission_numbers == []
    assert _regex_extract(body="paper 123456").submission_numbers == []


# --- OpenReview forum id ---------------------------------------------------
def test_regex_forum_id_from_forum_link():
    result = _regex_extract(body="See https://openreview.net/forum?id=Ab3xY9kLm2 please.")
    assert result.openreview_forum_ids == ["Ab3xY9kLm2"]
    assert result.method == "regex_fallback"


def test_regex_forum_id_from_pdf_link():
    result = _regex_extract(body="PDF: https://openreview.net/pdf?id=Zz9QwErTy1")
    assert result.openreview_forum_ids == ["Zz9QwErTy1"]


def test_regex_forum_id_rejects_group_link():
    """AAAI committee/group URLs share the ?id= shape — confirmed false positive."""
    result = _regex_extract(
        body="Join https://openreview.net/group?id=AAAI.org/2026/Conference today."
    )
    assert result.openreview_forum_ids == []


def test_regex_forum_id_rejects_bare_id_parameter():
    """A ?id= on any other host or path is not a forum id."""
    for text in [
        "https://example.com/thing?id=Ab3xY9kLm2",
        "https://openreview.net/profile?id=Ab3xY9kLm2",
        "see ?id=Ab3xY9kLm2",
    ]:
        assert _regex_extract(body=text).openreview_forum_ids == [], text


def test_regex_forum_id_rejects_wrong_length():
    """Exactly 10 chars — a longer id must not be truncated into a false match."""
    assert _regex_extract(
        body="https://openreview.net/forum?id=Ab3xY9kLm2Extra24Chars"
    ).openreview_forum_ids == []
    assert _regex_extract(
        body="https://openreview.net/forum?id=Short1"
    ).openreview_forum_ids == []


def test_regex_forum_id_found_in_subject():
    result = _regex_extract(subject="Re: openreview.net/forum?id=Ab3xY9kLm2")
    assert result.openreview_forum_ids == ["Ab3xY9kLm2"]


def test_regex_forum_id_preserves_case():
    """The id is opaque and case-sensitive; matching must not normalize it."""
    assert (
        _regex_extract(body="openreview.net/forum?id=aB3Xy9KlM2").openreview_forum_ids
        == ["aB3Xy9KlM2"]
    )


# --- OpenReview note id (Official Comment) ---------------------------------
# The note id must come from the SAME link as its forum id. Everything here
# either proves that pairing holds or proves the field stays None; the
# `openreview_forum_ids` assertions are repeated throughout on purpose, because
# this change is required to be strictly additive to that field.
_REAL_SHAPE = "https://openreview.net/forum?id=ll0avn6ylq&noteId=jnHgRMHgrm"


def test_regex_note_id_from_forum_link_id_before_note_id():
    """The ordering OpenReview's own notification links use."""
    result = _regex_extract(body=f"Reply here: {_REAL_SHAPE}")
    assert result.openreview_note_id == "jnHgRMHgrm"
    assert result.openreview_forum_ids == ["ll0avn6ylq"]
    assert result.method == "regex_fallback"


def test_regex_note_id_from_pdf_link():
    result = _regex_extract(
        body="https://openreview.net/pdf?id=Ab3xY9kLm2&noteId=Zz9QwErTy1"
    )
    assert result.openreview_note_id == "Zz9QwErTy1"
    assert result.openreview_forum_ids == ["Ab3xY9kLm2"]


def test_regex_note_id_found_in_subject():
    result = _regex_extract(subject=f"Re: {_REAL_SHAPE}")
    assert result.openreview_note_id == "jnHgRMHgrm"


def test_regex_note_id_survives_html_escaped_ampersand():
    """A crudely de-HTML-ed body can leave `&amp;` between the parameters."""
    result = _regex_extract(
        body="https://openreview.net/forum?id=ll0avn6ylq&amp;noteId=jnHgRMHgrm"
    )
    assert result.openreview_note_id == "jnHgRMHgrm"
    assert result.openreview_forum_ids == ["ll0avn6ylq"]


def test_regex_note_id_preserves_case():
    """Opaque, case-sensitive token — IGNORECASE matching must not fold it."""
    result = _regex_extract(
        body="https://openreview.net/forum?id=ll0avn6ylq&noteId=jNhGrmHGRM"
    )
    assert result.openreview_note_id == "jNhGrmHGRM"


def test_regex_note_id_tolerates_trailing_sentence_punctuation():
    for text in [f"See {_REAL_SHAPE}.", f"See ({_REAL_SHAPE})", f"<{_REAL_SHAPE}>"]:
        assert _regex_extract(body=text).openreview_note_id == "jnHgRMHgrm", text


def test_regex_note_id_ignores_unrelated_query_parameters():
    result = _regex_extract(
        body="https://openreview.net/forum?id=ll0avn6ylq&referrer=x&noteId=jnHgRMHgrm&t=1"
    )
    assert result.openreview_note_id == "jnHgRMHgrm"
    assert result.openreview_forum_ids == ["ll0avn6ylq"]


# --- the field stays None ---------------------------------------------------
def test_regex_forum_link_without_note_id_leaves_note_id_none():
    """The forum id is reported exactly as before; only the scalar is absent."""
    result = _regex_extract(body="See https://openreview.net/forum?id=Ab3xY9kLm2 please.")
    assert result.openreview_note_id is None
    assert result.openreview_forum_ids == ["Ab3xY9kLm2"]
    assert result.method == "regex_fallback"


def test_regex_no_openreview_link_at_all_leaves_both_empty():
    result = _regex_extract(
        subject="Re: Your Submission 22336",
        body="Could you clarify the page limit?",
        sender=_SENDER,
        sender_name=_SENDER_NAME,
    )
    assert result.openreview_note_id is None
    assert result.openreview_forum_ids == []
    assert result.submission_numbers == ["22336"]
    assert result.method == "regex_fallback"


def test_regex_group_link_with_note_id_is_still_rejected():
    """`/group?id=` is a confirmed false positive — a noteId does not redeem it."""
    result = _regex_extract(
        body="https://openreview.net/group?id=AAAI.org/2026&noteId=jnHgRMHgrm"
    )
    assert result.openreview_note_id is None
    assert result.openreview_forum_ids == []


def test_regex_note_id_rejects_over_long_token():
    """Reject rather than truncate — the same rule the forum id follows."""
    result = _regex_extract(
        body="https://openreview.net/forum?id=ll0avn6ylq&noteId=" + "a" * 33
    )
    assert result.openreview_note_id is None
    assert result.openreview_forum_ids == ["ll0avn6ylq"]


def test_regex_note_id_alone_on_a_link_yields_nothing():
    """No forum id on the link, so there is nothing to attach the note to."""
    result = _regex_extract(body="https://openreview.net/forum?noteId=jnHgRMHgrm")
    assert result.openreview_note_id is None
    assert result.openreview_forum_ids == []


# --- pairing is per-link, never across links --------------------------------
def test_regex_note_id_is_never_paired_across_two_different_links():
    """THE safety property: a forum id from one link and a noteId from another
    would name a comment that does not exist in that forum, and would be
    indistinguishable from a real pair once stored."""
    result = _regex_extract(
        body=(
            "Paper: https://openreview.net/forum?id=Ab3xY9kLm2 "
            "Comment: https://openreview.net/forum?noteId=jnHgRMHgrm"
        )
    )
    assert result.openreview_note_id is None
    assert result.openreview_forum_ids == ["Ab3xY9kLm2"]


def test_regex_note_id_pairs_with_its_own_link_not_a_neighbouring_one():
    """Two complete links: the reported note must belong to the FIRST forum."""
    result = _regex_extract(
        body=(
            "https://openreview.net/forum?id=Ab3xY9kLm2&noteId=note111111 and "
            "https://openreview.net/forum?id=Zz9QwErTy1&noteId=note222222"
        )
    )
    assert result.openreview_note_id == "note111111"
    assert result.openreview_forum_ids == ["Ab3xY9kLm2", "Zz9QwErTy1"]


def test_find_note_pairs_returns_both_values_from_one_query():
    assert _find_openreview_note_pairs("", _REAL_SHAPE) == [
        ("ll0avn6ylq", "jnHgRMHgrm")
    ]


def test_find_note_pairs_reads_note_id_before_id_ordering():
    """The pair SCAN is order-agnostic even though the coherence gate currently
    withholds this shape — see the suppression test below."""
    assert _find_openreview_note_pairs(
        "", "https://openreview.net/forum?noteId=jnHgRMHgrm&id=ll0avn6ylq"
    ) == [("ll0avn6ylq", "jnHgRMHgrm")]


def test_find_note_pairs_skips_links_missing_either_parameter():
    assert (
        _find_openreview_note_pairs(
            "",
            "https://openreview.net/forum?id=Ab3xY9kLm2 "
            "https://openreview.net/forum?noteId=jnHgRMHgrm",
        )
        == []
    )


def test_first_note_id_for_withholds_a_note_whose_forum_is_unreported():
    pairs = [("Ab3xY9kLm2", "note111111")]
    assert _first_note_id_for(pairs, ["Ab3xY9kLm2"]) == "note111111"
    assert _first_note_id_for(pairs, ["Zz9QwErTy1"]) is None
    assert _first_note_id_for(pairs, []) is None


def test_regex_note_id_before_id_ordering_is_currently_suppressed():
    """PINNED CONSEQUENCE, not an endorsement.

    `?noteId=...&id=...` parses fine at the pair scan (proved above), but the
    forum-id pattern is anchored to a literal `?id=` and was deliberately left
    untouched by this change, so that ordering yields no forum id — and the
    coherence gate then withholds the note rather than orphaning it.

    OpenReview's own links put `id=` first, so this shape is not expected in
    real traffic; the scan handles it defensively because query-parameter order
    carries no guarantee once a link is forwarded or rewritten. If the forum-id
    pattern is ever widened, this test flips to a pairing assertion and no other
    change is needed.
    """
    result = _regex_extract(
        body="https://openreview.net/forum?noteId=jnHgRMHgrm&id=ll0avn6ylq"
    )
    assert result.openreview_forum_ids == []
    assert result.openreview_note_id is None


# --- the LLM path -----------------------------------------------------------
def test_llm_path_never_reports_a_note_id():
    """The distiller's contract has no note-id line, and its OPENREVIEW_ID line
    carries a bare id with the link discarded — so there is nothing to pair
    with. Explicitly None rather than incidentally None."""
    result = _extract(_distilled(openreview_ids_raw=["Ab3xY9kLm2"]))
    assert result.method == "llm_distiller"
    assert result.openreview_forum_ids == ["Ab3xY9kLm2"]
    assert result.openreview_note_id is None


def test_llm_path_does_not_regex_the_body_for_a_note_id():
    """Mutual exclusivity: a note id sitting in the raw body must NOT top up a
    present LLM result — that would make one result part-LLM, part-regex."""
    result = EmailExtractor().extract(
        "",
        f"Reply here: {_REAL_SHAPE}",
        _SENDER,
        _SENDER_NAME,
        _distilled(openreview_ids_raw=["ll0avn6ylq"]),
    )
    assert result.openreview_note_id is None


def test_extraction_result_note_id_defaults_to_none():
    assert ExtractionResult().openreview_note_id is None


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
    assert result.submission_numbers == []
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
    assert result.submission_numbers == []
    assert result.openreview_forum_ids == []
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
    assert result.submission_numbers == ["22336"]
    assert result.openreview_forum_ids == ["Ab3xY9kLm2"]
    assert len(result.authors) == 1
    assert result.method == "regex_fallback"


def test_regex_fallback_never_raises_on_odd_input():
    result = EmailExtractor().extract("", "", "", None, None)
    assert result.method == "none"
